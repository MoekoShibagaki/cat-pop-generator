import os
import io
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/presentations']

def get_gapi_service(service_name, version):
    creds_json = json.loads(os.environ.get('GOOGLE_CREDENTIALS'))
    creds = service_account.Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    return build(service_name, version, credentials=creds)

def main():
    image_id = os.environ.get('IMAGE_ID')
    copy_id = os.environ.get('COPY_ID')

    print(f"DEBUG: IMAGE_ID={image_id}, COPY_ID={copy_id}")

    text_responses = json.loads(os.environ.get('TEXT_RESPONSES', '{}'))

    drive_service = get_gapi_service('drive', 'v3')
    slides_service = get_gapi_service('slides', 'v1')

    requests_body = []
    
    # 1. テキスト置換リクエストの作成
    for key, value in text_responses.items():
        val_str = value[0] if isinstance(value, list) else str(value)
        requests_body.append({
            "replaceAllText": {
                "containsText": {"text": f"{{{{{key}}}}}", "matchCase": True},
                "replaceText": val_str
            }
        })

    # 2. 保護団体名に応じた「文字色」変更リクエストの作成
    group_name_list = text_responses.get('保護団体名', [])
    group_name = group_name_list[0] if group_name_list else ""
    
    color_rgb = None
    if "もふもふ堂松本" in group_name:
        color_rgb = {"red": 0.36, "green": 0.42, "blue": 0.30}
    elif "もふもふ塩尻" in group_name:
        color_rgb = {"red": 0.29, "green": 0.36, "blue": 0.43}

    # スライドの要素を取得
    presentation = slides_service.presentations().get(presentationId=copy_id).execute()
    slides = presentation.get('slides', [])
    
    if slides:
        slide = slides[0]
        page_id = slide.get('objectId')
        
        # スライド内の各要素をチェック
        for element in slide.get('pageElements', []):
            desc = (element.get('description', '') or '').strip()
            title = (element.get('title', '') or '').strip()
            
            # ① 代替テキストが「色変更エリア」かつ、該当する団体名だった場合、文字色を変更
            if '色変更エリア' in desc or '色変更エリア' in title:
                if color_rgb:
                    requests_body.append({
                        "updateTextStyle": {
                            "objectId": element.get('objectId'),
                            "textRange": {"type": "ALL"},
                            "style": {
                                "foregroundColor": {
                                    "opaqueColor": {
                                        "rgbColor": color_rgb
                                    }
                                }
                            },
                            "fields": "foregroundColor"
                        }
                    })
                    print(f"DEBUG: 団体名「{group_name}」用の文字色変更リクエストを追加しました。")

            # ② 画像の流し込み処理（アスペクト比維持・はみ出し防止トリミング対応）
            if image_id and ('写真' in desc or '写真' in title):
                web_url = f"https://drive.google.com/uc?export=download&id={image_id}"
                
                try:
                    drive_service.permissions().create(
                        fileId=image_id,
                        body={'type': 'anyone', 'role': 'reader'},
                        supportsAllDrives=True
                    ).execute()
                    
                    # 画像のオリジナルサイズ（解像度）をDrive APIから取得
                    image_metadata = drive_service.files().get(
                        fileId=image_id, 
                        fields="imageMediaMetadata"
                    ).execute()
                    
                    media_meta = image_metadata.get('imageMediaMetadata', {})
                    img_width = media_meta.get('width')
                    img_height = media_meta.get('height')
                    
                    if not img_width or not img_height:
                        print("WARNING: 画像サイズが取得できなかったため、通常の引き伸ばし配置を行います。")
                        img_width, img_height = 1, 1
                        
                except Exception as e:
                    print(f"WARNING: 画像メタデータの取得に失敗しました: {e}")
                    img_width, img_height = 1, 1

                # テンプレート側の図形枠のサイズとアフィン変換（位置・拡大率）の情報を取得
                box_size = element.get('size', {})
                box_width = box_size.get('width', {}).get('magnitude', 1)
                box_height = box_size.get('height', {}).get('magnitude', 1)
                
                transform = element.get('transform', {})
                scale_x = transform.get('scaleX', 1)
                scale_y = transform.get('scaleY', 1)
                shear_x = transform.get('shearX', 0)
                shear_y = transform.get('shearY', 0)
                tx = transform.get('translateX', 0)
                ty = transform.get('translateY', 0)
                
                # スライド上の実際の表示サイズを割り出す
                actual_box_width = box_width * scale_x
                actual_box_height = box_height * scale_y
                
                box_ratio = actual_box_width / actual_box_height
                img_ratio = img_width / img_height
                
                crop_left = 0.0
                crop_right = 0.0
                crop_top = 0.0
                crop_bottom = 0.0
                
                # 新しい変換マトリクスの初期化（位置の補正用）
                new_scale_x = scale_x
                new_scale_y = scale_y
                new_tx = tx
                new_ty = ty

                # 【ロジックの修正点】
                # Google Slides API の仕様に合わせ、実際の枠にフィットさせた状態から
                # どれだけ「画像がはみ出すべきか」の比率を再計算し、アフィンマトリクスとトリミング量を完全同期させます
                if img_ratio > box_ratio:
                    # 【横長画像】
                    # 縦を実際の枠高に合わせ、左右を拡大してはみ出させる
                    excess_ratio = (img_ratio - box_ratio) / img_ratio
                    crop_left = excess_ratio / 2.0
                    crop_right = excess_ratio / 2.0
                    
                    # 左右にはみ出した分、配置開始位置（X）を左にズラして中央寄せ
                    new_scale_x = scale_y * (img_width / box_width) * (box_height / img_height)
                    actual_img_width = actual_box_height * img_ratio
                    new_tx = tx - (actual_img_width - actual_box_width) / 2.0
                else:
                    # 【縦長画像】
                    # 横を実際の枠幅に合わせ、上下を拡大してはみ出させる
                    excess_ratio = (box_ratio - img_ratio) / box_ratio
                    
                    # 枠のベースサイズ基準で切り抜きオフセットを正しく計算
                    fit_height_in_box = box_width / img_ratio
                    crop_top = ((fit_height_in_box - box_height) / fit_height_in_box) / 2.0
                    crop_bottom = crop_top
                    
                    # 上下にはみ出した分、配置開始位置（Y）を上にズラして中央寄せ
                    new_scale_y = scale_x * (img_height / box_height) * (box_width / img_width)
                    actual_img_height = actual_box_width / img_ratio
                    new_ty = ty - (actual_img_height - actual_box_height) / 2.0

                # 新しい画像要素の固有IDを定義
                new_image_object_id = f"InsertedImage_{element.get('objectId')}"

                # 1. 拡大・中央寄せを反映した状態で画像を生成するリクエスト
                requests_body.insert(0, {
                    "createImage": {
                        "objectId": new_image_object_id,
                        "elementProperties": {
                            "pageObjectId": page_id,
                            "transform": {
                                "scaleX": new_scale_x,
                                "scaleY": new_scale_y,
                                "shearX": shear_x,
                                "shearY": shear_y,
                                "translateX": new_tx,
                                "translateY": new_ty,
                                "unit": transform.get('unit', 'PT')
                            },
                            "size": box_size
                        },
                        "url": web_url
                    }
                })
                
                # 2. はみ出た余白を綺麗にカット（トリミング）するリクエスト
                requests_body.append({
                    "updateImageProperties": {
                        "objectId": new_image_object_id,
                        "imageProperties": {
                            "cropProperties": {
                                "leftOffset": crop_left,
                                "rightOffset": crop_right,
                                "topOffset": crop_top,
                                "bottomOffset": crop_bottom
                            }
                        },
                        "fields": "cropProperties"
                    }
                })

                # 3. テンプレートの元の図形（枠）を消去するリクエスト
                requests_body.append({
                    "deleteObject": {
                        "objectId": element.get('objectId')
                    }
                })
                print("DEBUG: 縦横比維持・枠ぴったりトリミングの画像リクエストを生成しました。")

    # すべてのリクエストをまとめて実行
    if requests_body:
        try:
            slides_service.presentations().batchUpdate(presentationId=copy_id, body={"requests": requests_body}).execute()
            print("DEBUG: スライドの文字置換・画像流し込み（枠内ぴったりトリミング）・文字色変更に成功しました。")
        except Exception as e:
            print(f"❌ Googleスライドの更新(batchUpdate)に失敗しました: {e}")
        
    print("Python process completed successfully!")

if __name__ == "__main__":
    main()
