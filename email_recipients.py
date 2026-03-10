import gspread
from google.oauth2.service_account import Credentials
import json
import os

SPREADSHEET_ID = "1pygw_harekyhCjjPdMClKU7qDxpZF-IKhNi2oh1qFCM"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Fallback en caso de que no haya credenciales (dev local, etc.)
FALLBACK = {
    "Dragon": [
        "sergiosar@gmail.com",
        "sergio@kobo.cl",
        "alvaro@kobo.cl",
        "ianmcharboe@gmail.com",
        "nanogarcia@gmail.com",
        "jcarrasco@zinvestments.cl",
        "thomasbertiez@gmail.com",
        "anremar@gmail.com",
    ],
    "Iberic": [
        "anremar@gmail.com",
        "sergiosar@gmail.com",
    ],
}


def get_recipients(sheet_name: str) -> list[str]:
    """Lee emails de la pestaña indicada ('Dragon' o 'Iberic') desde Google Sheets."""
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not creds_json:
        print(f"  ! GOOGLE_SHEETS_CREDENTIALS not set — using fallback")
        return FALLBACK.get(sheet_name, [])

    try:
        creds = Credentials.from_service_account_info(
            json.loads(creds_json), scopes=SCOPES
        )
        gc = gspread.authorize(creds)
        ws = gc.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
        emails = [r.strip() for r in ws.col_values(1)[1:] if r.strip()]
        print(f"  Loaded {len(emails)} recipients from Google Sheets ({sheet_name})")
        return emails
    except Exception as e:
        print(f"  ! Google Sheets error: {e} — using fallback")
        return FALLBACK.get(sheet_name, [])
