# 🔐 AWS Credential Security Guide

## ⚠️ CRITICAL SECURITY RULES

1. **NEVER hardcode AWS credentials in Python files**
2. **ALWAYS use environment variables via .env file**
3. **NEVER commit .env file to git**
4. **Rotate credentials immediately if exposed**

## 🛡️ Secure Setup

### Step 1: Create .env File

```bash
cd backend
cp .env.example .env
```

### Step 2: Add Your AWS Credentials

Edit `.env` file:

```bash
# AWS Credentials (Get from AWS Console → IAM → Security Credentials)
AWS_ACCESS_KEY_ID=your-actual-access-key-here
AWS_SECRET_ACCESS_KEY=your-actual-secret-key-here
AWS_STORAGE_BUCKET_NAME=rejlers-engineering-data
AWS_S3_REGION_NAME=me-central-1
```

### Step 3: Use Secure Credential Helper

```python
# ✅ CORRECT WAY
from aws_credential_helper import get_aws_client

s3_client = get_aws_client('s3')
buckets = s3_client.list_buckets()
```

```python
# ❌ WRONG WAY - NEVER DO THIS!
access_key = 'AKIA2SRLV2W7...'  # ❌ NEVER HARDCODE
secret_key = '2fx7ExZsOZ6...'  # ❌ NEVER HARDCODE
```

## 🔄 If Credentials Are Exposed

1. **Rotate immediately in AWS Console**:
   - Go to: AWS Console → IAM → Users → Security Credentials
   - Click "Make inactive" on exposed key
   - Create new access key
   - Update `.env` file with new credentials

2. **Check git history**:
   ```bash
   git log --all --full-history --source -- "*credentials*"
   ```

3. **Remove from git history** (if committed):
   ```bash
   git filter-branch --force --index-filter \
     "git rm --cached --ignore-unmatch download_legends.py" \
     --prune-empty --tag-name-filter cat -- --all
   ```

## 📋 Secure File Patterns

All credential-using scripts should follow this pattern:

```python
"""
Your script description
"""
import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from aws_credential_helper import get_aws_client, validate_aws_credentials
except ImportError:
    print("❌ Error: aws_credential_helper not found")
    sys.exit(1)

def main():
    # Validate credentials exist
    if not validate_aws_credentials():
        print("❌ AWS credentials not configured. Please update .env file.")
        return
    
    # Use secure client
    try:
        s3_client = get_aws_client('s3')
        # Your S3 operations here
    except Exception as e:
        print(f"❌ Failed: {e}")
        return

if __name__ == "__main__":
    main()
```

## 🚫 Files Removed from Git

These files had hardcoded credentials and have been removed:

- `verify_pfd_database.py`
- `search_legends_file.py`
- `analyze_legends_combine.py`
- `extract_legends_combine.py`
- `deep_analyze_legends.py`
- `extract_comprehensive_legends.py`
- `scan_s3_for_legends.py`
- `extract_all_legends.py`
- `test_assembly_folder.py`
- `download_legends.py`
- `download_roboflow_data.py`

## ✅ Verification

Test your credentials:

```bash
cd backend
python aws_credential_helper.py
```

You should see:
```
✅ AWS credentials found and validated
✅ Access Key: AKIA2SRLV2...****
✅ Secret Key: 2fx7ExZsOZ...****
✅ Region: me-central-1
✅ S3 connection successful! Found X buckets
```

## 📚 Resources

- [AWS Security Best Practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [Python Decouple Documentation](https://pypi.org/project/python-decouple/)
- [Git Secrets Tool](https://github.com/awslabs/git-secrets)
