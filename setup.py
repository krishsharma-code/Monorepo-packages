from setuptools import find_packages, setup


setup(
    name="shieldgate",
    version="0.1.0",
    description="Token-bucket rate limiting middleware for FastAPI and Flask",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    packages=find_packages(),
    python_requires=">=3.9",
    extras_require={
        "fastapi": ["fastapi>=0.95"],
        "flask": ["Flask>=2.2"],
        "test": ["pytest>=7", "fastapi>=0.95", "Flask>=2.2"],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
    ],
)
