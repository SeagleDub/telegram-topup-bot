"""
Пакет обработчиков для телеграм бота
"""

# Импортируем все модули обработчиков
from handlers import common
from handlers import topup
from handlers import supplies
from handlers import landing
from handlers import unicalization
from handlers import pixel
from handlers import broadcast
from handlers import translation

# Список всех роутеров для удобного импорта
__all__ = [
    'common',
    'topup',
    'supplies',
    'landing',
    'unicalization',
    'pixel',
    'broadcast',
    'translation'
]

