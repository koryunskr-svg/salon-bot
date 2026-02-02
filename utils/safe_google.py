@retry_google_api()  # ← декоратор ДОЛЖЕН быть
def safe_append_to_sheet(spreadsheet_id, sheet_name, values):
    print("\n" + "="*80)
    print("🔧🔧🔧 DEBUG SAFE_APPEND_TO_SHEET ВЫЗВАНА!")
    # ... принты ...
    
    credentials = get_google_credentials()
    if not credentials:
        print("❌ Нет credentials для Google API")
        return False
    
    try:  # ← try-except ВНУТРИ функции ДОЛЖЕН быть
        service = build('sheets', 'v4', credentials=credentials)
        body = {'values': values}
        print(f"🔧 Отправляю запрос к Google Sheets...")
        
        result = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=sheet_name,
            valueInputOption='RAW',
            body=body
        ).execute()
        
        print(f"🔧 Google Sheets ответил: {result}")
        print(f"✅ Добавлено {result.get('updates', {}).get('updatedCells', 0)} ячеек в {sheet_name}")
        return True

    except Exception as e:  # ← except ДОЛЖЕН быть
        print(f"❌❌❌ ОШИБКА в safe_append_to_sheet: {e}")
        import traceback
        traceback.print_exc()
        return False
