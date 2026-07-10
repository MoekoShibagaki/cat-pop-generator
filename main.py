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

    # 2. 画像の流し込み（正しいAPIコマンドに修正）
    if image_id:
        try:
            # フォームからアップロードされた画像（IMAGE_ID）の閲覧権限を公開にする
            try:
                drive_service.permissions().create(
                    fileId=image_id,
                    body={'type': 'anyone', 'role': 'reader'},
                    supportsAllDrives=True
                ).execute()
            except Exception:
                pass # 既に権限がある場合はスキップ
            
            # Google APIが読み込める公開ダウンロードURL
            web_url = f"https://drive.google.com/uc?export=download&id={image_id}"

            # 💡 修正：GoogleスライドAPIの正しい置換コマンドに変更
            # 代替テキストの「説明(description)」に「写真」と入っている図形をすべて画像に置き換えます
            requests_body.insert(0, {
                "replaceAllShapesWithImage": {
                    "imageUrl": web_url,
                    "imageReplaceMethod": "CENTER_CROP",
                    "containsText": {
                        "text": "写真",
                        "matchCase": False
                    }
                }
            })
            print("DEBUG: 画像置換リクエストを正しく作成しました。")
        except Exception as e:
            print(f"❌ 画像処理中にエラーが発生しました: {e}")

    # すべてのリクエストをまとめて実行
    if requests_body:
        try:
            slides_service.presentations().batchUpdate(presentationId=copy_id, body={"requests": requests_body}).execute()
            print("DEBUG: スライドの文字置換および画像流し込みに成功しました。")
        except Exception as e:
            print(f"❌ Googleスライドの更新(batchUpdate)に失敗しました: {e}")
        
    print("Python process completed successfully!")

if __name__ == "__main__":
    main()
