from setuptools import setup

import themis

setup(
    name='themis',
    version=themis.__version__,
    packages=['themis'],
    entry_points={
        'console_scripts': ['themis=themis.main:main'],
    },
    url='https://github.ibm.com/WatsonTooling/data-science',
    license='Apache Software License',
    author='W.P. McNeill',
    author_email='wmcneill@us.ibm.com',
    description='Watson performance analysis toolkit'
)
