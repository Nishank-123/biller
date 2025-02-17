import os
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from utils.pdf_generator import generate_pdf
import re

# Configure logging
logging.basicConfig(level=logging.DEBUG)

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
app = Flask(__name__)

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
app.secret_key = "fabrication-bill-secret"

# Initialize database
db.init_app(app)

# Ensure directories exist
if not os.path.exists('pdfs'):
    os.makedirs('pdfs')

def sanitize_filename(filename):
    # Remove invalid filename characters and spaces
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Replace spaces with underscores
    filename = filename.replace(' ', '_')
    return filename

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/bills')
def view_bills():
    from models import Bill
    status = request.args.get('status', '')
    
    query = Bill.query
    if status:
        query = query.filter(Bill.payment_status == status.lower())
    
    bills = query.order_by(Bill.date.desc()).all()
    return render_template('bills.html', bills=bills, selected_status=status)

@app.route('/generate_bill', methods=['POST'])
def generate_bill():
    try:
        from models import Bill, BillItem
        bill_data = request.get_json()

        # Create new bill
        bill = Bill(
            bill_number=datetime.now().strftime("%Y%m%d%H%M%S"),
            customer_name=bill_data['customerName'],
            phone_number=bill_data['phoneNumber'],
            date=datetime.strptime(bill_data['date'], '%Y-%m-%d').date(),
            subtotal=bill_data['subtotal'],
            total=bill_data['total'],
            paid_amount=bill_data.get('paidAmount', 0.0),
            payment_status=bill_data.get('paymentStatus', 'pending')
        )

        # Add items
        for item_data in bill_data['items']:
            item = BillItem(
                description=item_data['description'],
                quantity=item_data['quantity'],
                rate=item_data['rate'],
                amount=item_data['amount']
            )
            bill.items.append(item)

        db.session.add(bill)
        db.session.commit()

        # Generate PDF
        pdf_path = generate_pdf({
            'id': bill.bill_number,
            'customerName': bill.customer_name,
            'phoneNumber': bill.phone_number,
            'date': bill.date.strftime('%Y-%m-%d'),
            'items': [{
                'description': item.description,
                'quantity': item.quantity,
                'rate': item.rate,
                'amount': item.amount
            } for item in bill.items],
            'subtotal': bill.subtotal,
            'total': bill.total,
            'paid_amount': bill.paid_amount,
            'pending_amount': bill.total - bill.paid_amount,
            'payment_status': bill.payment_status
        })

        return jsonify({
            'success': True,
            'message': 'Bill generated successfully',
            'bill_id': bill.bill_number
        })
    except Exception as e:
        logging.error(f"Error generating bill: {str(e)}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

@app.route('/download_pdf/<bill_id>')
def download_pdf(bill_id):
    try:
        from models import Bill
        bill = Bill.query.filter_by(bill_number=bill_id).first()
        if not bill:
            return "Bill not found", 404

        # Create a sanitized filename with customer name
        sanitized_name = sanitize_filename(bill.customer_name)
        download_name = f'fabrication_bill_{sanitized_name}_{bill.date.strftime("%Y%m%d")}.pdf'

        pdf_path = os.path.join(os.getcwd(), "pdfs", f"{bill.bill_number}.pdf")
        return send_file(
            pdf_path,
            download_name=download_name,
            as_attachment=True
        )
    except Exception as e:
        logging.error(f"Error downloading PDF: {str(e)}")
        return "PDF not found", 404

@app.route('/merge_bills', methods=['POST'])
def merge_bills():
    try:
        from models import Bill, BillItem
        data = request.get_json()
        bill_ids = data['bill_ids']
        bills = Bill.query.filter(Bill.bill_number.in_(bill_ids)).all()
        
        if not bills:
            return jsonify({'error': 'No bills found'}), 404

        # Create new merged bill
        merged_bill = Bill(
            bill_number=datetime.now().strftime("%Y%m%d%H%M%S"),
            customer_name=bills[0].customer_name,
            phone_number=bills[0].phone_number,
            date=datetime.now().date(),
            subtotal=sum(bill.subtotal for bill in bills),
            total=sum(bill.total for bill in bills),
            paid_amount=sum(bill.paid_amount for bill in bills),
            payment_status='pending'
        )

        # Merge items
        for bill in bills:
            for item in bill.items:
                new_item = BillItem(
                    description=f"{item.description} (from bill {bill.bill_number})",
                    quantity=item.quantity,
                    rate=item.rate,
                    amount=item.amount
                )
                merged_bill.items.append(new_item)

        db.session.add(merged_bill)
        db.session.commit()

        # Generate PDF for merged bill
        generate_pdf({
            'id': merged_bill.bill_number,
            'customerName': merged_bill.customer_name,
            'phoneNumber': merged_bill.phone_number,
            'date': merged_bill.date.strftime('%Y-%m-%d'),
            'items': [{
                'description': item.description,
                'quantity': item.quantity,
                'rate': item.rate,
                'amount': item.amount
            } for item in merged_bill.items],
            'subtotal': merged_bill.subtotal,
            'total': merged_bill.total,
            'paid_amount': merged_bill.paid_amount,
            'pending_amount': merged_bill.total - merged_bill.paid_amount,
            'payment_status': merged_bill.payment_status
        })

        return jsonify({
            'success': True,
            'merged_bill_id': merged_bill.bill_number
        })
    except Exception as e:
        logging.error(f"Error merging bills: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/update_payment/<bill_id>', methods=['POST'])
def update_payment(bill_id):
    try:
        from models import Bill
        data = request.get_json()
        bill = Bill.query.filter_by(bill_number=bill_id).first()
        if not bill:
            return "Bill not found", 404
            
        new_paid_amount = float(data['paid_amount'])
        if new_paid_amount > bill.total:
            return "Paid amount cannot exceed total amount", 400
            
        bill.paid_amount = new_paid_amount
        bill.payment_status = 'paid' if new_paid_amount >= bill.total else 'partial'
        
        db.session.commit()
        return jsonify({
            'success': True,
            'new_status': bill.payment_status,
            'pending_amount': bill.pending_amount
        })
    except Exception as e:
        logging.error(f"Error updating payment: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/delete_bill/<bill_id>', methods=['DELETE'])
def delete_bill(bill_id):
    try:
        from models import Bill
        bill = Bill.query.filter_by(bill_number=bill_id).first()
        if not bill:
            return "Bill not found", 404
            
        if bill.payment_status != 'paid':
            return "Can only delete fully paid bills", 403
            
        # Delete the PDF file if it exists
        pdf_path = os.path.join(os.getcwd(), "pdfs", f"{bill.bill_number}.pdf")
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
            
        db.session.delete(bill)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        logging.error(f"Error deleting bill: {str(e)}")
        return jsonify({'error': str(e)}), 500

def init_db():
    with app.app_context():
        import models
        db.create_all()
        logging.info("Database tables created successfully")

# Initialize database tables
init_db()