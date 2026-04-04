from setuptools import setup, find_packages

setup(
    name="omni_router",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "openai>=1.0.0",
        "pydantic>=2.0.0"
    ],
    description="A multi-provider AI model router with failover logic.",
)
