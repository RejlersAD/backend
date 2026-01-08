import boto3
import os

print("Testing Direct S3 Access...")
print(f"Credentials configured: {bool(os.getenv('AWS_ACCESS_KEY_ID'))}")

s3 = boto3.client(
    's3',
    region_name='me-central-1',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
)

try:
    buckets = s3.list_buckets()
    print(f"\n✅ Found {len(buckets['Buckets'])} S3 buckets:")
    for b in buckets['Buckets']:
        print(f"   - {b['Name']}")
        
    # Test rejlers-engineering-data
    bucket = 'rejlers-engineering-data'
    print(f"\n📁 Testing access to {bucket}...")
    response = s3.list_objects_v2(Bucket=bucket, Prefix='ADNOC_P&IDs/', Delimiter='/', MaxKeys=5)
    
    projects = [p['Prefix'] for p in response.get('CommonPrefixes', [])]
    print(f"✅ Found {len(projects)} ADNOC projects:")
    for proj in projects[:5]:
        print(f"   - {proj}")
        
except Exception as e:
    print(f"❌ Error: {e}")
