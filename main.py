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

    # 2. 【新機能】保護団体名に応じた色変更リクエストの作成
    # フォームから送信された団体名を取得
    group_name_list = text_responses.get('保護団体名', [])
    group_name = group_name_list[0] if group_name_list else ""
    
    # ★設定部分★ お好みの色（RGB）を 0.0 〜 1.0 の間で指定してください
    color_rgb = None
    if "もふもふ堂松本" in group_name:
        # 例：優しい薄緑色 (R:220, G:245, B:220) -> 255で割った値を指定
        color_rgb = {"red": 0.86, "green": 0.96, "blue": 0.86}
    elif "もふもふ塩尻" in group_name:
        # 例：優しい薄青色 (R:220, G:235, B:255) -> 255で割った値を指定
        color_rgb = {"red": 0.86, "green": 0.92, "blue": 1.0}

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
            
            # ① 代替テキストが「色変更エリア」かつ、該当する団体名だった場合、色変更コマンドを追加
            if '色変更エリア' in desc or '色変更エリア' in title:
                if color_rgb:
                    requests_body.append({
                        "updateShapeProperties": {
                            "objectId": element.get('objectId'),
                            "shapeProperties": {
                                "shapeBackgroundFill": {
                                    "solidFill": {
                                        "color": {
                                            "rgbColor": color_rgb
                                        }
                                    }
                                }
                            },
                            "fields": "shapeBackgroundFill.solidFill.color"
                        }
                    })
                    print(f"DEBUG: 団体名「{group_name}」用の色変更リクエストを追加しました。")

            # ② 画像の流し込み処理（前回成功した確実なコード）
            if image_id and ('写真' in desc or '写真' in title):
                web_url = f"https://drive.google.com/uc?export=download&id={image_id}"
                
                # 閲覧権限を公開にする
                try:
                    drive_service.permissions().create(
                        fileId=image_id,
                        body={'type': 'anyone', 'role': 'reader'},
                        supportsAllDrives=True
                    ).execute()
                except Exception:
                    pass
                
                # 画像を配置する命令（先頭に挿入）
                requests_body.insert(0, {
                    "createImage": {
                        "elementProperties": {
                            "pageObjectId": page_id,
                            "transform": element['transform'],
                            "size": element['size']
                        },
                        "url": web_url
                    }
                })
                # 元の図形を削除する命令
                requests_body.append({
                    "deleteObject": {
                        "objectId": element.get('objectId')
                    }
                })
                print("DEBUG: ピンポイント画像配置リクエストを作成しました。")

    # すべてのリクエストをまとめて実行
    if requests_body:
        try:
            slides_service.presentations().batchUpdate(presentationId=copy_id, body={"requests": requests_body}).execute()
            print("DEBUG: スライドの文字置換・画像流し込み・色変更に成功しました。")
        except Exception as e:
            print(f"❌ Googleスライドの更新(batchUpdate)に失敗しました: {e}")
        
    print("Python process completed successfully!")

if __name__ == "__main__":
    main()
