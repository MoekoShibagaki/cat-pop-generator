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

    # 2. 画像の流し込み（固有ID指定方式にアップデート）
    if image_id:
        try:
            # フォームからアップロードされた画像の閲覧権限を公開にする
            try:
                drive_service.permissions().create(
                    fileId=image_id,
                    body={'type': 'anyone', 'role': 'reader'},
                    supportsAllDrives=True
                ).execute()
            except Exception:
                pass
            
            # Google APIが読み込める公開ダウンロードURL
            web_url = f"https://drive.google.com/uc?export=download&id={image_id}"

            # スライドの要素を調べて、代替テキストに「写真」がある枠を1件だけ特定する
            presentation = slides_service.presentations().get(presentationId=copy_id).execute()
            slides = presentation.get('slides', [])
            
            if slides:
                slide = slides[0]
                target_element_id = None
                
                for element in slide.get('pageElements', []):
                    desc = (element.get('description', '') or '').strip()
                    title = (element.get('title', '') or '').strip()
                    
                    if '写真' in desc or '写真' in title:
                        target_element_id = element.get('objectId')
                        print(f"DEBUG: 差し替え対象の枠IDを確定しました: {target_element_id}")
                        break

                # 💡 修正：特定した枠ID（objectId）に対して、直接画像を生成して配置する命令
                if target_element_id:
                    requests_body.insert(0, {
                        "createImage": {
                            "elementProperties": {
                                "pageId": slide.get('pageId'),
                                # 元の図形と全く同じ位置・サイズに画像を配置する
                                "transform": element['transform'],
                                "size": element['size']
                            },
                            "url": web_url
                        }
                    })
                    
                    # 💡 元のグレーの枠（図形）が残ると邪魔なので、画像配置と同時に元の図形を削除する命令を追加
                    requests_body.append({
                        "deleteObject": {
                            "objectId": target_element_id
                        }
                    })
                    print("DEBUG: ピンポイント画像配置（createImage）リクエストを作成しました。")
                else:
                    print("DEBUG: 代替テキストに『写真』が含まれる枠が見つかりませんでした。")
                    
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
