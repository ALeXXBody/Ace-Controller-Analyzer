from setuptools import setup, find_packages

setup(
    name="cd3217-analyzer",
    version="0.2.0",
    description="ACA - ACE Controller Analyzer: I2C diagnostic tool for Apple ACE1/ACE2 (CD3215/CD3217/CD3218) USB-C PD controllers",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "customtkinter>=5.2.0",
        "smbus2>=0.4.0",
    ],
    extras_require={
        "ftdi": ["pyftdi>=0.54.0"],
        "gui": ["customtkinter>=5.2.0"],
        "dev": ["pytest>=7.0", "pytest-cov>=4.0"],
        "build": ["pyinstaller>=6.0"],
    },
    entry_points={
        "console_scripts": [
            "cd3217-analyzer=cd3217_analyzer.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: System :: Hardware :: Hardware Drivers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
