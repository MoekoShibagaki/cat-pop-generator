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
    
    # ★設定部分★ トンマナに合わせた「くすみカラー」を指定
    color_rgb = None
    if "もふもふ堂松本" in group_name:
        # 🍵 パターン1：くすみ抹茶 (#5c6b4d)
        color_rgb = {"red": 0.36, "green": 0.42, "blue": 0.30}
        # ※もしパターン2（くすみ若草色 #688f70）にする場合は、下の行の「#」を消して、上の行の先頭に「#」をつけてください
        # color_rgb = {"red": 0.41, "green": 0.56, "blue": 0.44}
        
    elif "もふもふ塩尻" in group_name:
        # 🌌 パターン1：くすみ紺 (#4a5b6e)
        color_rgb = {"red": 0.29, "green": 0.36, "blue": 0.43}
        # ※もしパターン2（くすみラベンダー #6b728e）にする場合は、下の行の「#」を消して、上の行の先頭に「#」をつけてください
        # color_rgb = {"red": 0.42, "green": 0.45, "blue": 0.56}

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
                            "textRange": {
                                "type": "ALL"
                            },
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

            # ② 画像の流し込み処理
            if image_id and ('写真' in desc or '写真' in title):
                web_url = f"https://drive.google.com/uc?export=download&id={image_id}"
                
                try:
                    drive_service.permissions().create(
                        fileId=image_id,
                        body={'type': 'anyone', 'role': 'reader'},
                        supportsAllDrives=True
                    ).execute()
                except Exception:
                    pass
                
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
            print("DEBUG: スライドの文字置換・画像流し込み・文字色変更に成功しました。")
        except Exception as e:
            print(f"❌ Googleスライドの更新(batchUpdate)に失敗しました: {e}")
        
    print("Python process completed successfully!")

if __name__ == "__main__":
    main()
