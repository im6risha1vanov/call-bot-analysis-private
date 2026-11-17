from crm.callcenter_base import sheet_competitor, sheet_competitor_index


def row(**kw):
    return {'phone': '79991234567', 'note': 'Конкурент', 'comment': '', 'source_sheet_id': 's',
            'project': 'B1', 'base': '2026-11-01', 'tag': '',
            'attempts': [{'date': '2026-11-17', 'result': 'Нет ответа'}], **kw}


def match(rows, day='2026-11-17'):
    return sheet_competitor(sheet_competitor_index({'oksana': {'Картонная упаковка': rows}}), 'oksana', '9991234567', day)


def test_explicit_marker_with_matching_dated_attempt():
    marker = match([row()])
    assert marker['project'] == 'packaging'
    assert marker['provenance'] == 'sheet_operator'
    assert marker['marker'] == 'конкурент'
    assert match([row(note='', comment='КОНКУРЕНТЫ')])['marker'] == 'конкуренты'


def test_no_inference_from_other_notes_or_missing_attempt():
    for note in ['неактуально', 'заинтересованный', 'возможно конкурент', 'не конкурент', 'конкурент, уточнить']:
        assert match([row(note=note)]) is None
    assert match([row(attempts=[{'date': '2026-11-17', 'result': ''}])]) is None
    assert match([row()], '2026-11-18') is None
    assert match([row(note='конкурент', comment='не конкурент')]) is None


def test_ambiguous_multiple_source_rows_and_unknown_project():
    assert match([row(), row()]) is None
    assert sheet_competitor(sheet_competitor_index({'oksana': {'Другой проект': [row()]}}), 'oksana', '9991234567', '2026-11-17') is None


def test_index_includes_unmarked_rows_and_separates_operators_dates():
    books = {'oksana': {'Картонная упаковка': [row(), row(note='неактуально')]},
             'olga': {'Картонная упаковка': [row()]}}
    index = sheet_competitor_index(books)
    assert sheet_competitor(index, 'oksana', '9991234567', '2026-11-17') is None
    assert sheet_competitor(index, 'olga', '9991234567', '2026-11-17')['marker'] == 'конкурент'
    assert sheet_competitor(index, 'olga', '9991234567', '2026-11-18') is None
