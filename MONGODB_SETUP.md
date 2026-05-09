# Local MongoDB Setup Guide

## For Windows

### Option 1: Using MongoDB Community Edition (Recommended)

1. **Download MongoDB**
   - Visit: https://www.mongodb.com/try/download/community
   - Download the Windows Installer (MSI)
   - Run the installer and follow the setup wizard
   - Choose "Install MongoDB as a Service" during installation

2. **Start MongoDB Service**
   - MongoDB will start automatically if installed as a service
   - Or manually start it via:
     ```
     net start MongoDB
     ```

3. **Verify MongoDB is Running**
   ```
   mongosh
   ```
   You should see a prompt like: `test>`

### Option 2: Using Docker (If you have Docker installed)

```powershell
docker run -d -p 27017:27017 --name mongodb mongo:latest
```

### Option 3: Download MongoDB Portable (No Installation)

1. Download from: https://www.mongodb.com/try/download/community
2. Extract to a folder (e.g., `C:\mongodb`)
3. Create a data directory: `C:\mongodb\data`
4. Run MongoDB:
   ```
   C:\mongodb\bin\mongod.exe --dbpath C:\mongodb\data
   ```

## Configuration

Your `.env` file is already set to:
```
MONGO_URI=mongodb://localhost:27017/contact_book
```

This connects to:
- Host: localhost (your computer)
- Port: 27017 (default MongoDB port)
- Database: contact_book

## Troubleshooting

If you get a connection error:

1. **Check if MongoDB is running:**
   ```
   mongosh
   ```

2. **If mongosh fails, start MongoDB service:**
   ```
   net start MongoDB
   ```

3. **Check MongoDB logs:**
   - Windows Service logs are in: `C:\Program Files\MongoDB\Server\[version]\log\mongod.log`

4. **Verify port 27017 is accessible:**
   ```
   netstat -ano | findstr :27017
   ```

## After Setup

1. Ensure MongoDB is running
2. Run your Flask app:
   ```
   python run.py
   ```
3. Open http://127.0.0.1:5000
4. Start adding contacts!

## Stop MongoDB (when not needed)

```
net stop MongoDB
```
