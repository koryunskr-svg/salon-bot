def safe_sort_sheet_records(spreadsheet_id: str) -> bool:
    """
    Сортирует лист 'Записи' по дате (колонка G) и времени (колонка H).
    Возвращает True при успехе, False при ошибке.
    """
    try:
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON") or os.getenv("GOOGLE_CREDENTIALS")
        if not creds_json:
            logger.error("❌ GOOGLE_CREDENTIALS_JSON не найден в окружении")
            return False

        try:
            creds_data = json.loads(creds_json)
        except json.JSONDecodeError:
            if os.path.exists(creds_json):
                with open(creds_json, 'r', encoding='utf-8') as f:
                    creds_data = json.load(f)
            else:
                logger.error(f"❌ Неверный формат кредов: {creds_json[:50]}...")
                return False

        creds = Credentials.from_service_account_info(
            creds_data,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        service = build('sheets', 'v4', credentials=creds)

        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_id = None
        for sheet in spreadsheet.get('sheets', []):
            if sheet.get('properties', {}).get('title') == 'Записи':
                sheet_id = sheet['properties']['sheetId']
                break

        if not sheet_id:
            logger.error("❌ Лист 'Записи' не найден в таблице")
            return False

        body = {
            "requests": [{
                "sortRange": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 2,
                        "endRowIndex": 10000,
                        "startColumnIndex": 0,
                        "endColumnIndex": 15
                    },
                    "sortSpecs": [
                        {"dimensionIndex": 6, "sortOrder": "ASCENDING"},  # Дата (G)
                        {"dimensionIndex": 7, "sortOrder": "ASCENDING"}   # Время (H)
                    ]
                }
            }]
        }

        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=body
        ).execute()

        logger.info("✅ Сортировка листа 'Записи' выполнена")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка сортировки: {e}", exc_info=True)
        return False
