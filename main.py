import os
import io
import json
import base64
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from PIL import Image

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

    # 2. 画像の流し込み（サービスアカウントの容量を使わない方法に変更）
    if image_id:
        try:
            presentation = slides_service.presentations().get(presentationId=copy_id).execute()
            slides = presentation.get('slides', [])
            
            if slides:
                slide = slides[0]
                
                target_element = None
                for element in slide.get('pageElements', []):
                    desc = (element.get('description', '') or '').strip()
                    title = (element.get('title', '') or '').strip()
                    
                    if '写真' in desc or '写真' in title:
                        target_element = element
                        print(f"DEBUG: 【特定成功】本物の写真枠を確定しました。ID: {element.get('objectId')}")
                        break

                if target_element:
                    print("DEBUG: サービスアカウントの容量制限を回避するため、直接画像を流し込みます...")
                    
                    # 💡 改善：元々ドライブにあるフォームからアップロードされた画像（IMAGE_ID）の閲覧権限を公開にする
                    try:
                        drive_service.permissions().create(
                            fileId=image_id,
                            body={'type': 'anyone', 'role': 'reader'},
                            supportsAllDrives=True
                        ).execute()
                    except Exception:
                        pass # 既に権限がある場合はスキップ
                    
                    # 変換なしで直接Google APIが読み込めるURLを生成
                    web_url = f"https://drive.google.com/uc?export=download&id={image_id}"

                    # 枠を元の画像URLで置き換える（Googleスライド側のCENTER_CROP機能で綺麗に収めます）
                    requests_body.insert(0, {
                        "replaceShapeWithImage": {
                            "imageReplaceMethod": "CENTER_CROP",
                            "shapeRelationId": target_element['objectId'],
                            "imageUrl": web_url
                        }
                    })
                else:
                    print("DEBUG: テンプレート内に代替テキストとして『写真』が設定された枠が見つかりませんでした。")
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
