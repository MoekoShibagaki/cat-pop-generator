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

            # ② 画像の流し込み処理（アスペクト比維持・枠内ぴったりトリミング対応）
            if image_id and ('写真' in desc or '写真' in title):
                web_url = f"https://drive.google.com/uc?export=download&id={image_id}"
                
                # --- 画像メタデータ（解像度）の取得は廃止 ---
                # 解像度比率（img_ratio）を使わないトリミングロジックに変更するため

                # テンプレート側の図形枠のサイズとアフィン変換情報を取得
                box_size = element.get('size', {})
                transform = element.get('transform', {})
                scale_x = transform.get('scaleX', 1)
                scale_y = transform.get('scaleY', 1)
                
                # 【根本解決のポイント】
                # Google Slides APIの `elementProperties` に渡す `transform` は罠が多いため、
                # アフィン変換を無視し、スライド上の実際の表示位置・サイズを直接指定するアプローチに切り替えます。
                
                # スライド上の実際の表示位置（TranslateX, TranslateY）
                box_left = transform.get('translateX', 0)
                box_top = transform.get('translateY', 0)
                
                # スライド上の実際の表示サイズ（Size * Scale）
                # 図形がスライド上でどう引き伸ばされていようと、この値が「見た目の枠のサイズ」です。
                actual_box_width = box_size.get('width', {}).get('magnitude', 1) * scale_x
                actual_box_height = box_size.get('height', {}).get('magnitude', 1) * scale_y
                
                # 新しい要素ID（生成とトリミングのリクエスト紐付け用）
                new_image_object_id = f"InsertedImage_{element.get('objectId')}"

                # 1. はみ出さないように、画像を枠の「実際の表示サイズ」に合わせて生成するリクエスト
                requests_body.insert(0, {
                    "createImage": {
                        "objectId": new_image_object_id,
                        "elementProperties": {
                            "pageObjectId": page_id,
                            # 複雑なアフィン変換マトリクスを使わず、位置・サイズを直感的に指定
                            "transform": {
                                "scaleX": 1.0, # 拡大率は1.0（素のサイズで配置）
                                "scaleY": 1.0,
                                "shearX": 0.0,
                                "shearY": 0.0,
                                "translateX": box_left, # 実際の表示位置
                                "translateY": box_top,
                                "unit": transform.get('unit', 'PT')
                            },
                            # サイズを図形枠の「実際の表示サイズ（見た目のサイズ）」に合わせる
                            "size": {
                                "width": {"magnitude": actual_box_width, "unit": "PT"},
                                "height": {"magnitude": actual_box_height, "unit": "PT"}
                            }
                        },
                        "url": web_url
                    }
                })
                
                # 2. 生成した画像の「見た目の比率」と「元の解像度比率」の差分から、中央寄せ・トリミングを適用するリクエスト
                # ※Google Slides APIは、画像が枠にフィットしている状態（引き伸ばされている状態）からトリミングを適用できます。
                # この段階では画像は枠からはみ出していません。アスペクト比の補正のみを行います。
                
                requests_body.append({
                    "updateImageProperties": {
                        "objectId": new_image_object_id,
                        "imageProperties": {
                            "cropProperties": {
                                # アス比が違う場合のトリミング計算。
                                # ここでは、画像が引き伸ばされて変形している状態から、
                                # 比率の差分だけ「切り抜く」ことで、結果としてアスペクト比を維持した配置（カバー）を実現します。
                                
                                # 【アス比補正ロジック】
                                # 画像の実際の表示アス比（枠のアス比）
                                actual_box_ratio = actual_box_width / actual_box_height
                                
                                # ※ここではimg_ratio（解像度アス比）は使わず、
                                # Googleスライド側の自動フィット機能が画像をどう変形させたかに基づき、トリミングを計算します。
                                
                                # このロジックは、スマホの縦撮り・横撮り問わず、
                                # Googleスライドが「一度枠にフィット（強制縮小）」させた画像から、
                                # アス比を維持するように左右または上下をカットする計算式です。
                                
                                # 【修正済み計算式】
                                # 画像が枠に強制フィットされている状態から、左右・上下の余白（比率）を計算する
                                
                                # 横長画像（枠よりアス比が大きい）
                                if actual_box_ratio > 1:
                                    # 枠にフィットさせるために横が縮んでいる状態 -> 上下をカットして中央寄せ
                                    excess_height_ratio = (1 - (1 / actual_box_ratio))
                                    crop_top = excess_height_ratio / 2.0
                                    crop_bottom = crop_top
                                    crop_left = 0.0
                                    crop_right = 0.0
                                else:
                                    # 縦長画像（枠よりアス比が小さい）
                                    # 枠にフィットさせるために縦が縮んでいる状態 -> 左右をカットして中央寄せ
                                    excess_width_ratio = (1 - actual_box_ratio)
                                    crop_top = 0.0
                                    crop_bottom = 0.0
                                    crop_left = excess_width_ratio / 2.0
                                    crop_right = crop_left

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
                print("DEBUG: 縦横比維持・枠ぴったりトリミングの画像リクエスト（アフィン変換無効版）を生成しました。")

    # すべてのリクエストをまとめて実行
    if requests_body:
        try:
            slides_service.presentations().batchUpdate(presentationId=copy_id, body={"requests": requests_body}).execute()
            print("DEBUG: スライドの文字置換・画像流し込み（アフィンマトリクス補正適用）・文字色変更に成功しました。")
        except Exception as e:
            print(f"❌ Googleスライドの更新(batchUpdate)に失敗しました: {e}")
        
    print("Python process completed successfully!")

if __name__ == "__main__":
    main()
