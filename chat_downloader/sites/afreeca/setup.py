from setuptools import setup, find_packages

setup(
    name                = 'afreeca',
    version             = '0.5.8',
    description         = 'AfreecaTV API Wrapper',
    author              = 'minibox',
    author_email        = 'minibox724@gmail.com',
    url                 = 'https://github.com/wakscord/afreeca',
    install_requires    = ['aiohttp', 'orjson', 'typing_extensions'],
    packages            = find_packages(exclude = []),
    keywords            = ['afreeca'],
    python_requires     = '>=3.9',
    package_data        = {},
    zip_safe            = False,
    classifiers         = [
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
    ],
)
