# Web Encoder v2 (photos + S3 + scope toggle)

Copy these files into:
`D:\Projects\CatalogAutomation\SQLite\`

## Install
```bat
pip install flask pillow boto3
```
Optional (better background removal):
```bat
pip install rembg onnxruntime
```

## Run
```bat
python web_encoder.py
```

## AWS
- Bucket: ansonsupermart.com
- Prefix: images/
- Region: ap-southeast-1
- Uses boto3 credentials (env vars or ~/.aws/credentials)

## Filename rule
- Prefer CanonicalBarcode (primary barcode) if present
- else fallback to MERKEY
