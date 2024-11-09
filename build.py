import os
import json
import shutil
import subprocess
import logging
from pathlib import Path
import fnmatch
from typing import Set, Dict, Any, List
import re

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('plugin_builder.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class PluginBuilderError(Exception):
    """Базовый класс для исключений при сборке плагина"""
    pass


class PluginPathError(PluginBuilderError):
    """Ошибка при работе с путями"""
    pass


class PluginConfigError(PluginBuilderError):
    """Ошибка в конфигурации плагина"""
    pass


class GitIgnorePattern:
    def __init__(self, pattern: str):
        self.pattern = pattern
        self.is_dir_only = pattern.endswith('/')
        self.is_negation = pattern.startswith('!')

        # Удаляем ! для паттернов исключения
        if self.is_negation:
            pattern = pattern[1:]

        # Преобразуем паттерн в формат fnmatch
        self.regex = self._convert_pattern_to_regex(pattern.rstrip('/'))

    def _convert_pattern_to_regex(self, pattern: str) -> str:
        """Преобразует gitignore паттерн в regex"""
        if not pattern:
            return ""

        # Экранируем специальные символы regex
        pattern = re.escape(pattern)

        # Заменяем gitignore wildcards на regex эквиваленты
        pattern = pattern.replace(r'\?', '[^/]')
        pattern = pattern.replace(r'\*\*', '.*')
        pattern = pattern.replace(r'\*', '[^/]*')

        # Если паттерн не начинается с /, он может соответствовать файлам в любой директории
        if not pattern.startswith('/'):
            pattern = f'(.*/)?{pattern}'
        else:
            pattern = pattern[1:]  # Удаляем начальный /

        return f'^{pattern}(/.*)?$'

    def matches(self, path: str) -> bool:
        """Проверяет, соответствует ли путь паттерну"""
        # Нормализуем путь для Windows
        path = path.replace('\\', '/')
        # Удаляем начальный слеш если есть
        if path.startswith('/'):
            path = path[1:]

        return bool(re.match(self.regex, path))


class PluginBuilder:
    def __init__(self):
        self.flow_launcher_plugins_path = os.path.expanduser("~\\AppData\\Roaming\\FlowLauncher\\Plugins")
        self.plugin_source_path = os.path.join(os.getcwd())
        self.plugin_json_path = os.path.join(self.plugin_source_path, "plugin.json")
        self.lib_path = os.path.join(self.flow_launcher_plugins_path, "lib")
        self.gitignore_path = os.path.join(self.plugin_source_path, ".gitignore")
        self.ignore_patterns: List[GitIgnorePattern] = []

    def load_plugin_info(self) -> Dict[str, Any]:
        """Загружает и валидирует информацию о плагине из plugin.json"""
        try:
            if not os.path.exists(self.plugin_json_path):
                raise PluginConfigError(f"Файл plugin.json не найден в {self.plugin_json_path}")

            with open(self.plugin_json_path, 'r', encoding='utf-8') as file:
                plugin_info = json.load(file)

            required_fields = ['Name', 'Version']
            missing_fields = [field for field in required_fields if field not in plugin_info]
            if missing_fields:
                raise PluginConfigError(f"В plugin.json отсутствуют обязательные поля: {', '.join(missing_fields)}")

            return plugin_info
        except json.JSONDecodeError as e:
            raise PluginConfigError(f"Ошибка парсинга plugin.json: {str(e)}")
        except Exception as e:
            raise PluginConfigError(f"Неожиданная ошибка при чтении plugin.json: {str(e)}")

    def load_gitignore(self) -> None:
        """Загружает паттерны исключений из .gitignore"""
        self.ignore_patterns = []
        try:
            # Добавляем стандартные исключения
            default_patterns = [
                # Файлы сборки и скрипты
                'build_plugin.py',
                'plugin_builder.log',
                'setup.py',
                'publish.py',
                'build.py',
                'deploy.py',

                # Конфигурационные файлы Git и GitHub
                '.git/',
                '.gitignore',
                '.github/',
                '.gitattributes',
                '.gitlab/',
                '.gitlab-ci.yml',

                # Лицензии и документация
                'LICENSE',
                'LICENSE.txt',
                'LICENSE.md',
                'CHANGELOG.md',
                'CONTRIBUTING.md',
                'CODE_OF_CONDUCT.md',
                'README.md',

                # Кэш и временные файлы Python
                '__pycache__/',
                '*.pyc',
                '*.pyo',
                '*.pyd',
                '.pytest_cache/',
                '.coverage',
                'htmlcov/',
                '.tox/',

                # Виртуальные окружения
                '.venv/',
                'venv/',
                '.env/',
                'env/',

                # IDE и редакторы
                '.idea/',
                '.vs/',
                '.vscode/',
                '*.suo',
                '*.user',
                '*.sln',
                '*.swp',
                '*.sublime-*',

                # Системные файлы
                '.DS_Store',
                'Thumbs.db',
                'desktop.ini',

                # Файлы тестов
                'tests/',
                'test/',
                '*_test.py',
                '*_tests.py',
                'test_*.py',
                'tests_*.py',

                # Файлы разработки
                'requirements-dev.txt',
                'dev-requirements.txt',
                'requirements_dev.txt',
                'requirements_test.txt',
                'tox.ini',
                'mypy.ini',
                '.flake8',
                '.pylintrc',
                '.pre-commit-config.yaml'
            ]

            for pattern in default_patterns:
                self.ignore_patterns.append(GitIgnorePattern(pattern))

            if os.path.exists(self.gitignore_path):
                with open(self.gitignore_path, 'r', encoding='utf-8') as file:
                    for line in file:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            self.ignore_patterns.append(GitIgnorePattern(line))

            logger.info(f"Загружено {len(self.ignore_patterns)} паттернов исключений")
        except Exception as e:
            logger.warning(f"Ошибка при чтении .gitignore: {str(e)}. Используются только базовые исключения.")

    def should_ignore(self, file_path: str) -> bool:
        """Проверяет, нужно ли исключить файл"""
        # Получаем относительный путь
        relative_path = os.path.relpath(file_path, self.plugin_source_path)
        relative_path = relative_path.replace('\\', '/')

        is_ignored = False
        for pattern in self.ignore_patterns:
            if pattern.is_negation:
                # Если паттерн начинается с !, он отменяет предыдущие правила игнорирования
                if pattern.matches(relative_path):
                    is_ignored = False
            else:
                # Обычный паттерн игнорирования
                if pattern.matches(relative_path):
                    is_ignored = True

        return is_ignored

    def build_dependencies(self, build_path) -> None:
        """Устанавливает зависимости в папку lib внутри папки плагина"""
        try:
            requirements_path = os.path.join(self.plugin_source_path, "requirements.txt")
            if not os.path.exists(requirements_path):
                logger.info("Файл requirements.txt не найден, пропускаем установку зависимостей")
                return

            # Создаем папку lib внутри папки плагина
            self.lib_path = os.path.join(build_path, "lib")
            if os.path.exists(self.lib_path):
                logger.info(f"Очистка существующей папки lib: {self.lib_path}")
                shutil.rmtree(self.lib_path)

            logger.info(f"Создание папки lib: {self.lib_path}")
            os.makedirs(self.lib_path)

            logger.info("Установка зависимостей...")
            try:
                result = subprocess.run(
                    ["pip", "install", "-r", requirements_path, "-t", self.lib_path],
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding='utf-8'
                )
                logger.info(f"Зависимости успешно установлены в {self.lib_path}")
                logger.debug(f"Вывод pip:\n{result.stdout}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Ошибка при установке зависимостей:\n{e.stderr}")
                raise PluginBuilderError(f"Ошибка при установке зависимостей: {e.stderr}")

            # Очистка лишних файлы pip и кэша
            self._cleanup_lib_directory()

        except Exception as e:
            raise PluginBuilderError(f"Неожиданная ошибка при установке зависимостей: {str(e)}")

    def _cleanup_lib_directory(self) -> None:
        """Очищает папку lib от ненужных файлов pip"""
        if not self.lib_path or not os.path.exists(self.lib_path):
            return

        try:
            # Паттерны файлов и директорий для удаления
            patterns_to_remove = [
                '*.dist-info',
                '*.egg-info',
                '__pycache__',
                '*.pyc',
                '*.pyo',
                '*.pyd',
                '.pytest_cache',
                '.coverage',
                'htmlcov',
                'tests',
                'test',
                '*_test.py',
                '*_tests.py',
                'test_*.py'
            ]

            for root, dirs, files in os.walk(self.lib_path, topdown=False):
                # Удаление файлов
                for pattern in patterns_to_remove:
                    for item in fnmatch.filter(files + dirs, pattern):
                        path = os.path.join(root, item)
                        if os.path.isfile(path):
                            os.remove(path)
                            logger.debug(f"Удален файл: {path}")
                        elif os.path.isdir(path):
                            shutil.rmtree(path)
                            logger.debug(f"Удалена директория: {path}")

        except Exception as e:
            logger.warning(f"Ошибка при очистке директории lib: {str(e)}")

    def build_plugin(self, plugin_info: Dict[str, Any]) -> str:
        """Собирает плагин"""
        try:
            plugin_name_version = f"{plugin_info['Name']}-{plugin_info['Version']}"
            plugin_build_path = os.path.join(self.flow_launcher_plugins_path, plugin_name_version)

            logger.info(f"Начало сборки плагина {plugin_name_version}")

            if os.path.exists(plugin_build_path):
                logger.info(f"Удаление старой версии плагина: {plugin_build_path}")
                shutil.rmtree(plugin_build_path)
            os.makedirs(plugin_build_path)

            files_copied = 0
            ignored_files = 0

            for root, dirs, files in os.walk(self.plugin_source_path):
                # Фильтруем игнорируемые директории
                dirs[:] = [d for d in dirs if not self.should_ignore(os.path.join(root, d))]

                for file in files:
                    src_path = os.path.join(root, file)
                    if self.should_ignore(src_path):
                        ignored_files += 1
                        logger.debug(f"Игнорируется: {os.path.relpath(src_path, self.plugin_source_path)}")
                        continue

                    rel_path = os.path.relpath(src_path, self.plugin_source_path)
                    dest_path = os.path.join(plugin_build_path, rel_path)

                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    shutil.copy2(src_path, dest_path)
                    files_copied += 1
                    logger.debug(f"Скопирован файл: {rel_path}")

            logger.info(f"Плагин успешно собран в {plugin_build_path}")
            logger.info(f"Скопировано файлов: {files_copied}")
            logger.info(f"Игнорировано файлов: {ignored_files}")

            return plugin_build_path

        except OSError as e:
            raise PluginPathError(f"Ошибка при работе с файловой системой: {str(e)}")
        except Exception as e:
            raise PluginBuilderError(f"Неожиданная ошибка при сборке плагина: {str(e)}")


def main():
    """Основная функция сборки плагина"""
    builder = PluginBuilder()
    try:
        logger.info("Начало процесса сборки плагина")

        plugin_info = builder.load_plugin_info()
        logger.info(f"Загружена информация о плагине: {plugin_info['Name']} v{plugin_info['Version']}")

        builder.load_gitignore()

        build_path = builder.build_plugin(plugin_info)

        builder.build_dependencies(build_path)

        logger.info("Сборка плагина успешно завершена")
    except PluginBuilderError as e:
        logger.error(f"Ошибка при сборке плагина: {str(e)}")
        raise SystemExit(1)
    except Exception as e:
        logger.error(f"Критическая ошибка: {str(e)}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()