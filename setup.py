from setuptools import setup, find_packages

setup(
    name="payment_gateway",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "razorpay",
        "Flask",
        "mysql-connector-python",
    ],
    author="Your Name",
    author_email="your.email@example.com",
    description="A shared payment gateway integration for Flask applications",
    keywords="payment, gateway, razorpay, paypal, flask",
    url="",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
    ],
    python_requires=">=3.6",
)