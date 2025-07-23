from setuptools import setup, find_packages

setup(
    name="payment_gateway",
    version="1.0.1",
    packages=find_packages(),
    install_requires=[
        "razorpay>=1.4.2",                    # Flexible for future updates
        "Flask==3.1.1", 
        "mysql-connector-python==8.1.0",
        "cryptography==41.0.7",
        "requests==2.31.0",
        "python-dotenv==1.1.1",
    ],
    author="Manu Goel",
    author_email="manu@mgimpacts.com",
    description="A shared payment gateway integration for Flask applications",
    keywords="payment, gateway, razorpay, paypal, flask",
    url="",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
    ],
    python_requires=">=3.8",
)