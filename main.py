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

            # ② 画像の流し込み処理（アスペクト比維持・トリミング対応）
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
                        # 万が一メタデータからサイズが取れなかった場合のセーフティ
                        print("WARNING: 画像サイズが取得できなかったため、通常の引き伸ばし配置を行います。")
                        img_width, img_height = 1, 1
                        
                except Exception as e:
                    print(f"WARNING: 画像メタデータの取得に失敗しました: {e}")
                    img_width, img_height = 1, 1

                # テンプレート側の図形枠のサイズと位置情報を取得
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
                
                # 比率の計算
                box_ratio = box_width / box_height
                img_ratio = img_width / img_height
                
                crop_left = 0.0
                crop_right = 0.0
                crop_top = 0.0
                crop_bottom = 0.0
                
                # 新しいアフィン変換マトリクス（配置位置と拡大率）の初期化
                new_scale_x = scale_x
                new_scale_y = scale_y
                new_tx = tx
                new_ty = ty

                if img_ratio > box_ratio:
                    # 【横長画像（スマホ横向きなど）】
                    # 縦を枠に合わせ、左右をはみ出させてセンタリングし、トリミングする
                    excess_ratio = (img_ratio - box_ratio) / img_ratio
                    crop_left = excess_ratio / 2.0
                    crop_right = excess_ratio / 2.0
                    
                    # 拡大率と位置の補正
                    display_width = box_height * img_ratio
                    new_scale_x = scale_x * (display_width / box_width)
                    new_tx = tx - (scale_x * (display_width - box_width)) / 2.0
                else:
                    # 【縦長画像（スマホ縦向きなど）】
                    # 横を枠に合わせ、上下をはみ出させてセンタリングし、トリミングする
                    excess_ratio = (box_ratio - img_ratio) / box_ratio
                    # 比率ベースのトリミング値を計算
                    img_scale_in_box = box_width / img_width
                    display_height = img_height * img_scale_in_box
                    
                    crop_top = ((display_height - box_height) / display_height) / 2.0
                    crop_bottom = crop_top
                    
                    # 拡大率と位置の補正
                    new_scale_y = scale_y * (display_height / box_height)
                    new_ty = ty - (scale_y * (display_height - box_height)) / 2.0

                # 新しい要素IDを仮決定（画像を生成してからトリミングするため、IDを固定します）
                new_image_object_id = f"InsertedImage_{element.get('objectId')}"

                # 画像を少し大きめ（はみ出すサイズ）に配置するリクエスト
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
                
                # 配置した画像に対してトリミング（クロップ）を適用するリクエスト
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

                # 元の図形（枠）を削除するリクエスト
                requests_body.append({
                    "deleteObject": {
                        "objectId": element.get('objectId')
                    }
                })
                print("DEBUG: 縦横比維持・トリミング対応の画像配置リクエストを作成しました。")

    # すべてのリクエストをまとめて実行
    if requests_body:
        try:
            slides_service.presentations().batchUpdate(presentationId=copy_id, body={"requests": requests_body}).execute()
            print("DEBUG: スライドの文字置換・画像流し込み（トリミング最適化）・文字色変更に成功しました。")
        except Exception as e:
            print(f"❌ Googleスライドの更新(batchUpdate)に失敗しました: {e}")
        
    print("Python process completed successfully!")

if __name__ == "__main__":
    main()
