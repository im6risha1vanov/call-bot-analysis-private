"""Render only the four supplied excerpts, never query DB, ASR or a model."""
from pathlib import Path
from unified.render import expanded

FRAGMENT = '\n'.join([
    'Менеджер: Понял Вас. А рассчитать экономику и рассмотреть тарифы?',
    'Клиент: Да, было бы удобнее, если бы вы файлом отправили.',
    'Менеджер: Я вам тогда сейчас напишу, что у нас есть.',
    'Клиент: Да, давайте телеграмм. Удобнее по этому номеру привязан.',
])


def control_row():
    return {'transcript': FRAGMENT,
            'metadata': {'manager': 'Марк Амирханян', 'duration_seconds': 98, 'phone': 'номер не указан',
                         'call_started_at': '2026-10-06T10:38:00+03:00', 'timezone': 'Europe/Moscow',
                         'fragment_context': {'source': 'user_prompt', 'postponement': True, 'production_difficulties': True}},
            'configuration': {'show_full_phone': True, 'transcript_complete': False},
            'primary_analysis': {'score': None, 'analysis': {'rows': []}}, 'auxiliary': {}}


if __name__ == '__main__':
    path = Path(__file__).resolve().parents[1] / 'docs/Extended_review_control_fragment.txt'
    path.write_text(expanded(control_row()) + '\n', encoding='utf-8')
    print(path)
