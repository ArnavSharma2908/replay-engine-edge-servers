#!/usr/bin/env python3
"""Setup script for arl_deployer package."""

from setuptools import setup, find_packages
import os

# Read the contents of README file
this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='arl-deployer',
    version='0.1.0',
    description='Automated Trident customer configuration deployment tool',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='Trident QA Team',
    python_requires='>=3.8',
    packages=find_packages(exclude=['tests', 'tests.*']),
    install_requires=[
        'click>=8.0.0',
        'paramiko>=2.7.0',
        'pyyaml>=5.4.0',
        'requests>=2.25.0',
        'python-dateutil>=2.8.0',
    ],
    extras_require={
        'dev': [
            'pytest>=6.0.0',
            'pytest-cov>=2.10.0',
            'pytest-mock>=3.3.0',
            'black>=21.0',
            'flake8>=3.8.0',
            'mypy>=0.910',
        ],
    },
    entry_points={
        'console_scripts': [
            'arl-deploy=arl_deployer.cli:cli',
        ],
    },
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Testing',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
    ],
)
