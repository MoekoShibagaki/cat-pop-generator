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

                    if '{{写真}}' in desc or '{{写真}}' in title or '{{写真}}' in shape_text:
                        target_element = element
                        break

                if target_element and slide_id:
                    box_w = target_element['size']['width']['magnitude']
                    box_h = target_element['size']['height']['magnitude']

                    processed_img_bytes = crop_and_fit_image(img_bytes, box_w, box_h)
                    
                    # 💡 対策：切り抜いた画像ファイルを一度あなたの共有フォルダに一時保存
                    file_metadata = {
                        'name': 'tmp_cropped_cat_image.jpg',
                        'parents': [folder_id] if folder_id else []
                    }
                    media = MediaIoBaseUpload(io.BytesIO(processed_img_bytes), mimetype='image/jpeg')
                    tmp_file = drive_service.files().create(body=file_metadata, media_body=media, supportsAllDrives=True).execute()
                    tmp_file_id = tmp_file.get('id')
                    
                    # 誰でもリンクを知っていれば閲覧できるように一時的に権限変更（Googleスライド流し込み用）
                    drive_service.permissions().create(
                        fileId=tmp_file_id,
                        body={'type': 'anyone', 'role': 'reader'},
                        supportsAllDrives=True
                    ).execute()
                    
                    # Googleが確実に認識できる、ドライブの画像WebリンクURLを生成
                    web_url = f"https://docs.google.com/uc?export=download&id={tmp_file_id}"

                    requests_body.append({
                        "createImage": {
                            "elementProperties": {
                                "pageId": slide_id,
                                "size": target_element['size'],
                                "transform": target_element['transform']
                            },
                            "url": web_url
                        }
                    })
                    requests_body.append({"deleteObject": {"objectId": target_element['objectId']}})
        except Exception as e:
            print(f"Image processing skipped due to error: {e}")

    # リクエストの実行
    if requests_body:
        slides_service.presentations().batchUpdate(presentationId=copy_id, body={"requests": requests_body}).execute()
        
    # 💡 対策：使い終わった一時画像ファイルをGoogleドライブから完全に削除する
    if tmp_file_id:
        try:
            drive_service.files().delete(fileId=tmp_file_id, supportsAllDrives=True).execute()
            print("Temporary image file cleaned up successfully.")
        except Exception as e:
            print(f"Failed to delete temporary file: {e}")
        
    print("Python process completed successfully!")

if __name__ == "__main__":
    main()
