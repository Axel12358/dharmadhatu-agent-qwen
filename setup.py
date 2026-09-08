from setuptools import setup, find_packages

setup(
    name="dharmadhatu-agent",
    version="1.0.0",
    description="Psytrance event OSINT pipeline",
    author="Axel12358",
    python_requires=">=3.9",
    packages=find_packages(exclude=["tests*"]),
    install_requires=[
        "requests[socks]>=2.28.0",
        "beautifulsoup4>=4.11.0",
        "PySocks>=1.7.1",
        "duckduckgo-search>=4.0",
        "scikit-learn>=1.2.0",
    ],
    extras_require={
        "dev": [
            "pre-commit",
            "black",
            "isort",
            "flake8",
            "pytest",
        ],
    },
)
