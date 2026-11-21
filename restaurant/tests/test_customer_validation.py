"""
Integration tests for customer information validation
Tests the auth gate form and customer data handling
"""

from django.test import TestCase, Client
from django.urls import reverse
from restaurant.models import Customer, Order, Food, FoodCategory
import json


class CustomerValidationTests(TestCase):
    """Test customer information validation and auth gate"""

    def setUp(self):
        """Set up test client and test data"""
        self.client = Client()

        # Create test food category and food items
        self.category = FoodCategory.objects.create(
            name="تست",
            icon="🍔"
        )
        self.food = Food.objects.create(
            name="همبرگر تست",
            price=50000,
            description="برای تست",
            category=self.category,
            available=True
        )

    def test_customer_name_required(self):
        """Test that customer name is required"""
        order_data = {
            "table": 1,
            "seats": ["T01-top"],
            "phone": "09121234567",
            "foods": [{"id": self.food.id, "name": self.food.name, "price": self.food.price, "qty": 1}]
        }

        response = self.client.post(
            '/api/create_order/',
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('error', data)
        self.assertIn('نام', data['error'].lower())

    def test_customer_name_validation(self):
        """Test customer name validation rules"""
        # Empty name
        order_data = {
            "table": 1,
            "seats": ["T01-top"],
            "name": "",
            "phone": "09121234567",
            "foods": [{"id": self.food.id, "name": self.food.name, "price": self.food.price, "qty": 1}]
        }

        response = self.client.post(
            '/api/create_order/',
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)

        # Whitespace only
        order_data["name"] = "   "
        response = self.client.post(
            '/api/create_order/',
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)

    def test_customer_phone_optional(self):
        """Test that phone number is optional"""
        order_data = {
            "table": 1,
            "seats": ["T01-top"],
            "name": "علی",
            "foods": [{"id": self.food.id, "name": self.food.name, "price": self.food.price, "qty": 1}]
        }

        response = self.client.post(
            '/api/create_order/',
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])

    def test_customer_phone_validation(self):
        """Test phone number validation"""
        # Valid Iranian phone number
        order_data = {
            "table": 1,
            "seats": ["T01-top"],
            "name": "علی",
            "phone": "09121234567",
            "foods": [{"id": self.food.id, "name": self.food.name, "price": self.food.price, "qty": 1}]
        }

        response = self.client.post(
            '/api/create_order/',
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)

        # Invalid phone format
        order_data["phone"] = "123"
        response = self.client.post(
            '/api/create_order/',
            data=json.dumps(order_data),
            content_type='application/json'
        )

        # Should reject invalid phone format
        self.assertEqual(response.status_code, 400)

    def test_customer_check_by_phone(self):
        """Test customer lookup by phone number"""
        # Create a customer
        customer = Customer.objects.create(
            name="محمد",
            phone="09121234567"
        )

        # Check if customer exists
        response = self.client.get('/api/customer/check/?phone=09121234567')

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['found'])
        self.assertEqual(data['name'], "محمد")

    def test_customer_check_not_found(self):
        """Test customer lookup returns not found for new phone"""
        response = self.client.get('/api/customer/check/?phone=09999999999')

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertFalse(data['found'])

    def test_returning_customer_creates_single_record(self):
        """Test that returning customer doesn't create duplicate records"""
        order_data = {
            "table": 1,
            "seats": ["T01-top"],
            "name": "رضا",
            "phone": "09123456789",
            "foods": [{"id": self.food.id, "name": self.food.name, "price": self.food.price, "qty": 1}]
        }

        # First order
        response1 = self.client.post(
            '/api/create_order/',
            data=json.dumps(order_data),
            content_type='application/json'
        )
        self.assertEqual(response1.status_code, 200)

        # Second order with same phone
        order_data["table"] = 2
        response2 = self.client.post(
            '/api/create_order/',
            data=json.dumps(order_data),
            content_type='application/json'
        )
        self.assertEqual(response2.status_code, 200)

        # Should have only one customer record
        customer_count = Customer.objects.filter(phone="09123456789").count()
        self.assertEqual(customer_count, 1)

        # Should have two orders
        order_count = Order.objects.filter(customer__phone="09123456789").count()
        self.assertEqual(order_count, 2)

    def test_order_creation_with_valid_customer(self):
        """Test complete order creation with valid customer info"""
        order_data = {
            "table": 1,
            "seats": ["T01-top", "T01-right"],
            "name": "احمد",
            "phone": "09121234567",
            "foods": [
                {"id": self.food.id, "name": self.food.name, "price": self.food.price, "qty": 2}
            ]
        }

        response = self.client.post(
            '/api/create_order/',
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        self.assertIn('order_id', data)

        # Verify customer was created
        customer = Customer.objects.get(phone="09121234567")
        self.assertEqual(customer.name, "احمد")

        # Verify order was created
        order = Order.objects.get(id=data['order_id'])
        self.assertEqual(order.customer, customer)
        self.assertEqual(order.table, 1)
        self.assertEqual(order.total, 100000)  # 50000 * 2


class CustomerUIValidationTests(TestCase):
    """Test client-side validation behavior"""

    def test_auth_form_html_validation(self):
        """Test that auth form has proper HTML5 validation"""
        response = self.client.get('/customer/')

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Check for required attribute on name field
        self.assertIn('id="authName"', content)
        self.assertIn('required', content)

        # Check for optional phone field
        self.assertIn('id="authPhone"', content)

        # Check for proper input types
        self.assertIn('type="text"', content)
        self.assertIn('type="tel"', content)

    def test_auth_form_has_validation_hints(self):
        """Test that form has helpful validation hints"""
        response = self.client.get('/customer/')

        content = response.content.decode('utf-8')

        # Check for hint texts
        self.assertIn('auth-field-hint', content)
        self.assertIn('فقط برای ثبت داخل فاکتور', content)
        self.assertIn('اختیاری', content)


class LocalStorageSimulationTests(TestCase):
    """Test localStorage behavior through session simulation"""

    def test_session_persistence(self):
        """Test that customer data persists across requests"""
        # Simulate first visit - create order
        order_data = {
            "table": 1,
            "seats": ["T01-top"],
            "name": "حسین",
            "phone": "09121111111",
            "foods": [{"id": 1, "name": "Test", "price": 10000, "qty": 1}]
        }

        # Create customer through API
        customer = Customer.objects.create(name="حسین", phone="09121111111")

        # Simulate returning visit - check customer
        response = self.client.get('/api/customer/check/?phone=09121111111')

        data = json.loads(response.content)
        self.assertTrue(data['found'])
        self.assertEqual(data['name'], "حسین")
