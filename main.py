import os
import io
import json
import base64
from google.oauth2 import service_account
from googleapiclient.discovery import build
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
                    if '{{写真}}' in desc or '{{写真}}' in title:
                        target_element = element
                        break

                if target_element and slide_id:
                    box_w = target_element['size']['width']['magnitude']
                    box_h = target_element['size']['height']['magnitude']

                    processed_img_bytes = crop_and_fit_image(img_bytes, box_w, box_h)
                    
                    b64_data = base64.b64encode(processed_img_bytes).decode('utf-8')
                    data_url = f"data:image/jpeg;base64,{b64_data}"

                    requests_body.append({
                        "createImage": {
                            "elementProperties": {
                                "pageId": slide_id,
                                "size": target_element['size'],
                                "transform": target_element['transform']
                            },
                            "url": data_url
                        }
                    })
                    requests_body.append({"deleteObject": {"objectId": target_element['objectId']}})
        except Exception as e:
            print(f"Image processing skipped due to error: {e}")

    if requests_body:
        slides_service.presentations().batchUpdate(presentationId=copy_id, body={"requests": requests_body}).execute()
        
    print("Python process completed successfully!")

if __name__ == "__main__":
    main()
