import os
import io
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from PIL import Image

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/presentations']

def get_gapi_service(service_name, version):
    creds_json = json.loads(os.environ.get('GOOGLE_CREDENTIALS'))
    creds = service_account.Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    return build(service_name, version, credentials=creds)

def crop_and_fit_image(image_bytes, target_width, target_height):
    img = Image.open(io.BytesIO(image_bytes))
    try:
        if hasattr(img, '_getexif'):
            exif = img._getexif()
            if exif:
                orientation = exif.get(0x0112)
                if orientation == 3: img = img.rotate(180, expand=True)
                elif orientation == 6: img = img.rotate(270, expand=True)
                elif orientation == 8: img = img.rotate(90, expand=True)
    except Exception:
        pass

    orig_w, orig_h = img.size
    target_ratio = target_width / target_height
    orig_ratio = orig_w / orig_h

    if orig_ratio > target_ratio:
        new_h = orig_h
        new_w = int(orig_h * target_ratio)
        left = (orig_w - new_w) // 2
        top = 0
    else:
        new_w = orig_w
        new_h = int(orig_w / target_ratio)
        left = 0
        top = (orig_h - new_h) // 2

    cropped_img = img.crop((left, top, left + new_w, top + new_h))
    output = io.BytesIO()
    cropped_img.save(output, format="JPEG", quality=95)
    return output.getvalue()

def main():
    image_id = os.environ.get('IMAGE_ID')
    copy_id = os.environ.get('COPY_ID')
    folder_id = os.environ.get('FOLDER_ID')

    print(f"DEBUG: IMAGE_ID={image_id}, COPY_ID={copy_id}, FOLDER_ID={folder_id}")

    text_responses = json.loads(os.environ.get('TEXT_RESPONSES', '{}'))

    drive_service = get_gapi_service('drive', 'v3')
    slides_service = get_gapi_service('slides', 'v1')

    requests_body = []
    
    # 1. テキスト置換
    for key, value in text_responses.items():
        val_str = value[0] if isinstance(value, list) else str(value)
        requests_body.append({
            "replaceAllText": {
                "containsText": {"text": f"{{{{{key}}}}}", "matchCase": True},
                "replaceText": val_str
            }
        })

    tmp_file_id = None

    # 2. 画像の切り抜き＆流し込み
    if image_id:
        try:
            print("DEBUG: フォームからアップロードされた画像をダウンロード中...")
            img_request = drive_service.files().get_media(fileId=image_id, supportsAllDrives=True)
            img_bytes = img_request.execute()

            presentation = slides_service.presentations().get(presentationId=copy_id).execute()
            slides = presentation.get('slides', [])
            
            if slides:
                slide = slides[0]
                slide_id = slide.get('pageId')
                
                target_element = None
                for element in slide.get('pageElements', []):
                    desc = element.get('description', '') or ''
                    title = element.get('title', '') or ''
                    
                    shape_text = ""
                    if 'shape' in element and 'text' in element['shape']:
                        for paragraph in element['shape']['text'].get('textElements', []):
                            if 'textRun' in paragraph:
                                shape_text += paragraph['textRun'].get('content', '')

                    # 設定されている代替テキスト（説明やタイトル）をチェック
                    if '{{写真}}' in desc or '{{写真}}' in title or '{{写真}}' in shape_text:
                        target_element = element
                        print(f"DEBUG: テンプレート内の対象枠を発見しました。ID: {element.get('objectId')}")
                        break  # 💡 修正：見つかった時点でループを即座に抜ける（インデントの位置を修正）

                if target_element and slide_id:
                    box_w = target_element['size']['width']['magnitude']
                    box_h = target_element['size']['height']['magnitude']

                    print("DEBUG: 画像を枠のサイズに合わせて切り抜き中...")
                    processed_img_bytes = crop_and_fit_image(img_bytes, box_w, box_h)
                    
                    # 切り抜いた画像をGoogleドライブに一時保存
                    file_metadata = {'name': 'tmp_cropped_cat_image.jpg'}
                    if folder_id:
                        file_metadata['parents'] = [folder_id]
                        
                    media = MediaIoBaseUpload(io.BytesIO(processed_img_bytes), mimetype='image/jpeg')
                    tmp_file = drive_service.files().create(body=file_metadata, media_body=media, supportsAllDrives=True).execute()
                    tmp_file_id = tmp_file.get('id')
                    print(f"DEBUG: 一時画像を保存しました。ID: {tmp_file_id}")
                    
                    # 閲覧権限を公開に変更
                    drive_service.permissions().create(
                        fileId=tmp_file_id,
                        body={'type': 'anyone', 'role': 'reader'},
                        supportsAllDrives=True
                    ).execute()
                    
                    web_url = f"https://docs.google.com/uc?export=download&id={tmp_file_id}"

                    # 枠そのものを画像URLで直接置き換える
                    requests_body.append({
                        "replaceShapeWithImage": {
                            "imageReplaceMethod": "CENTER_CROP",
                            "shapeRelationId": target_element['objectId'],
                            "imageUrl": web_url
                        }
                    })
                else:
                    print("DEBUG: テンプレート内に『{{写真}}』の代替テキストを持つ枠が見つかりませんでした。")
        except Exception as e:
            print(f"❌ 画像処理中にエラーが発生しました: {e}")

    # リクエストの実行
    if requests_body:
        try:
            slides_service.presentations().batchUpdate(presentationId=copy_id, body={"requests": requests_body}).execute()
            print("DEBUG: スライドの文字置換および画像流し込みに成功しました。")
        except Exception as e:
            print(f"❌ Googleスライドの更新(batchUpdate)に失敗しました: {e}")
        
    # 使い終わった一時ファイルを削除
    if tmp_file_id:
        try:
            drive_service.files().delete(fileId=tmp_file_id, supportsAllDrives=True).execute()
            print("DEBUG: 一時画像を削除しました。")
        except Exception as e:
            print(f"DEBUG: 一時画像の削除に失敗（自動で消えるため問題ありません）: {e}")
        
    print("Python process completed successfully!")

if __name__ == "__main__":
    main()
