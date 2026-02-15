# Setup 1 – Status

## Done

- **.env**
  - Fixed `NOTIFICATION_EMAIL=` (was missing `=`).
  - Set `SMTP_HOST=smtp.gmail.com` for Gmail (reminders will use this).
  - Your handles, SMTP user, notification email, and CF/LC passwords are already set.

- **MongoDB**
  - Installed via: `winget install MongoDB.Server`.
  - To start MongoDB:
    1. Open a **new** PowerShell (so PATH includes MongoDB), then run:
       ```powershell
       cd e:\codeforces
       .\scripts\start_mongodb.ps1
       ```
    2. Or open **Services** (`services.msc`), find **MongoDB**, and start it.
  - Data directory used by the script: `D:\cp-assistant-cache\mongodb-data` (so C: is not used).

- **SMTP (Gmail)**
  - If you use 2FA, create an [App Password](https://myaccount.google.com/apppasswords) and set `SMTP_PASSWORD` in `.env` to that (not your normal Gmail password).

## Next

1. Start MongoDB (see above).
2. Run the app: `.\run_all.ps1` or `python run_api.py` and `python run_scheduler.py`.
3. Open http://localhost:8000 and click **Update Data** to sync from Codeforces and LeetCode into MongoDB.
