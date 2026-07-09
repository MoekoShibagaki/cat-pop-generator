import os
import io
import json
import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from PIL import Image

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/presentations']

def get_gapi_service(service_name, version):
    # GitHubのSecretsに保存したGoogleの認証JSONを読み込む
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
    template_id = os.environ.get('TEMPLATE_ID')
    folder_id = os.environ.get('FOLDER_ID')
    cat_name = os.environ.get('CAT_NAME', '保護猫')
    image_id = os.environ.get('IMAGE_ID')
    text_responses = json.loads(os.environ.get('TEXT_RESPONSES', '{}'))

    drive_service = get_gapi_service('drive', 'v3')
    slides_service = get_gapi_service('slides', 'v1')

    # 1. テンプレートスライドのコピーを作成
    copied_file = drive_service.files().copy(
        fileId=template_id,
        body={"name": f"{cat_name}_編集用一時ファイル", "parents": [folder_id]}
    ).execute()
    copy_id = copied_file.get('id')

    requests_body = []
    
    # 2. テキスト置換リクエストの作成
    for key, value in text_responses.items():
        val_str = value[0] if isinstance(value, list) else str(value)
        requests_body.append({
            "replaceAllText": {
                "containsText": {"text": f"{{{{{key}}}}}", "matchCase": True},
                "replaceText": val_str
            }
        })

    # 3. 画像の切り抜き＆挿入処理
    if image_id:
        img_request = drive_service.files().get_media(fileId=image_id)
        img_bytes = img_request.execute()

        presentation = slides_service.presentations().get(presentationId=copy_id).execute()
        slide = presentation.get('slides')[0]
        
        target_element = None
        for element in slide.get('pageElements', []):
            desc = element.get('description', '') or ''
            title = element.get('title', '') or ''
            if '{{写真}}' in desc or '{{写真}}' in title:
                target_element = element
                break

        if target_element:
            box_w = target_element['size']['width']['magnitude']
            box_h = target_element['size']['height']['magnitude']

            # アスペクト比を維持して中央切り抜き
            processed_img_bytes = crop_and_fit_image(img_bytes, box_w, box_h)

            media = MediaIoBaseUpload(io.BytesIO(processed_img_bytes), mimetype='image/jpeg', resumable=True)
            temp_img_file = drive_service.files().create(
                body={"name": "temp_processed_image.jpg", "parents": [folder_id]},
                media_body=media
            ).execute()
            temp_img_id = temp_img_file.get('id')

            drive_service.permissions().create(fileId=temp_img_id, body={"role": "reader", "type": "anyone"}).execute()
            web_url = drive_service.files().get(fileId=temp_img_id, fields='webContentLink').execute().get('webContentLink')

            requests_body.append({
                "createImage": {
                    "elementProperties": {
                        "pageId": slide['pageId'],
                        "size": target_element['size'],
                        "transform": target_element['transform']
                    },
                    "url": web_url
                }
            })
            requests_body.append({"deleteObject": {"objectId": target_element['objectId']}})

    if requests_body:
        slides_service.presentations().batchUpdate(presentationId=copy_id, body={"requests": requests_body}).execute()

    # 4. PDFに変換して保存
    pdf_request = drive_service.files().export_media(fileId=copy_id, mimeType='application/pdf')
    pdf_bytes = pdf_request.execute()

    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    pdf_media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype='application/pdf')
    drive_service.files().create(
        body={"name": f"{cat_name}_紹介カード_{timestamp}.pdf", "parents": [folder_id]},
        media_body=pdf_media
    ).execute()

    # 5. 一時ファイルの削除
    drive_service.files().delete(fileId=copy_id).execute()
    if image_id and 'temp_img_id' in locals():
        drive_service.files().delete(fileId=temp_img_id).execute()
        
    print("POP generated successfully via GitHub Actions!")

if __name__ == "__main__":
    main()
