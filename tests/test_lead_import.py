import base64
import io
import unittest
import zipfile

from crm import lead_import


def xlsx_payload() -> str:
    workbook = io.BytesIO()
    with zipfile.ZipFile(workbook, 'w', compression=zipfile.ZIP_DEFLATED) as file:
        file.writestr('xl/worksheets/sheet1.xml', """
        <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
          <row r="1"><c r="A1" t="inlineStr"><is><t>Имя</t></is></c>
            <c r="B1" t="inlineStr"><is><t>Телефон</t></is></c>
            <c r="C1" t="inlineStr"><is><t>Компания</t></is></c>
            <c r="D1" t="inlineStr"><is><t>Комментарий</t></is></c></row>
          <row r="2"><c r="A2" t="inlineStr"><is><t>Иван</t></is></c><c r="B2"><v>7.9991230002E+10</v></c>
            <c r="C2" t="inlineStr"><is><t>Ромашка</t></is></c><c r="D2" t="inlineStr"><is><t>Пункт; деталь</t></is></c></row>
          <row r="3"><c r="A3" t="inlineStr"><is><t>Без телефона</t></is></c></row>
        </sheetData></worksheet>""")
    return base64.b64encode(workbook.getvalue()).decode()


class LeadImportTests(unittest.TestCase):
    def test_excel_reads_header_numeric_phone_and_notes(self):
        found, invalid = lead_import.parse_file('очередь.xlsx', xlsx_payload())
        self.assertEqual(found, [{'number': '79991230002', 'name': 'Иван', 'company': 'Ромашка',
                                  'note': 'Пункт; деталь'}])
        self.assertEqual(invalid, ['строка 3'])

    def test_csv_windows_encoding_and_columns_without_header(self):
        raw = '8 (999) 123-00-01;Ирина;Ромашка;Заметка\n'.encode('cp1251')
        found, bad = lead_import.parse_file('очередь.csv', base64.b64encode(raw).decode())
        self.assertEqual((found[0]['number'], found[0]['company'], bad), ('79991230001', 'Ромашка', []))

    def test_invalid_base64_and_too_many_rows_rejected(self):
        with self.assertRaises(ValueError):
            lead_import.parse_file('очередь.xlsx', 'invalid!')
        with self.assertRaisesRegex(ValueError, 'до 5000'):
            lead_import.parse_rows([['79991230001']] * 5001)
