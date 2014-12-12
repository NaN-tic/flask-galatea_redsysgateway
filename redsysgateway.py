#This file is part redsysgateway blueprint for Flask.
#The COPYRIGHT file at the top level of this repository contains 
#the full copyright notices and license terms.
from flask import Blueprint, request, render_template, flash, current_app, g, \
    session, abort, url_for, redirect
from flask.ext.babel import gettext as _
from galatea.tryton import tryton
from galatea.csrf import csrf
from decimal import Decimal
from redsys import Client

redsysgateway = Blueprint('redsysgateway', __name__, template_folder='templates')

SHOP = current_app.config.get('TRYTON_SALE_SHOP')

Shop = tryton.pool.get('sale.shop')
Sequence = tryton.pool.get('ir.sequence')
GatewayTransaction = tryton.pool.get('account.payment.gateway.transaction')

@csrf.exempt
@redsysgateway.route('/ipn', methods=['POST'], endpoint="ipn")
@tryton.transaction()
def redsys_ipn(lang):
    """Signal Redsys confirmation payment

    Redys request form data:
     Ds_Date
     Ds_SecurePayment
     Ds_Card_Country
     Ds_AuthorisationCode
     Ds_MerchantCode
     Ds_Amount
     Ds_ConsumerLanguage
     Ds_Response
     Ds_Order
     Ds_TransactionType
     Ds_Terminal
     Ds_Signature
     Ds_Currency
     Ds_Hour
    """
    shop = Shop(SHOP)

    gateway = None
    for payment in shop.esale_payments:
        if payment.payment_type.gateway:
            payment_gateway = payment.payment_type.gateway
            if payment_gateway.method == 'redsys':
                gateway = payment_gateway
                break

    reference = request.form.get('Ds_Order')
    response = request.form.get('Ds_Response')
    amount = Decimal(request.form.get('Ds_Amount'))
    authorisation_code = request.form.get('Ds_AuthorisationCode')

    # Search transaction
    gtransactions = GatewayTransaction.search([
        ('reference_gateway', '=', reference),
        ], limit=1)
    if gtransactions:
        gtransaction, = gtransactions
        gtransaction.authorisation_code = authorisation_code
        gtransaction.amount = amount
        gtransaction.save()
    else:
        gtransaction = GatewayTransaction()
        gtransaction.description = reference
        gtransaction.authorisation_code = authorisation_code
        gtransaction.gateway = gateway
        gtransaction.reference_gateway = reference
        gtransaction.amount = amount
        gtransaction.save()

    # Process transaction
    if not int(response) < 100:
        return 'ko'

    # Confirm transaction 0000 to 0099
    GatewayTransaction.confirm([gtransaction])
    return 'ok'

@redsysgateway.route('/confirm', endpoint="confirm")
@tryton.transaction()
def redsys_confirm(lang):
    return render_template('redsys-confirm.html')

@redsysgateway.route('/cancel', endpoint="cancel")
@tryton.transaction()
def redsys_cancel(lang):
    return render_template('redsys-cancel.html')

@redsysgateway.route('/', methods=['POST'], endpoint="redsys")
@tryton.transaction()
def redsys_form(lang):
    shop = Shop(SHOP)

    base_url = current_app.config['BASE_URL']
    sandbox = current_app.config['DEBUG']

    gateway = None
    for payment in shop.esale_payments:
        if payment.payment_type.gateway:
            payment_gateway = payment.payment_type.gateway
            if payment_gateway.method == 'redsys':
                gateway = payment_gateway
                break

    if not gateway:
        abort(404)

    url_ipn = '%s%s' % (base_url, url_for('.ipn', lang=g.language))
    url_confirm = '%s%s' % (base_url, url_for('.confirm', lang=g.language))
    url_cancel = '%s%s' % (base_url, url_for('.cancel', lang=g.language))

    origin = request.form.get('origin')
    if not origin:
        abort(404)
    try:
        o = origin.split(',')
        r = tryton.pool.get(o[0])(o[1])
    except:
        abort(500)
    reference = request.form.get('reference')
    if getattr(r, 'total_amount'):
        total_amount = getattr(r, 'total_amount')
    else:
        flash(_("Error when get total amount to pay. Repeat or contact us."),
            "danger")
        redirect(url_for('/', lang=g.language))
    amount = total_amount - r.gateway_amount

    # Redsys force to use a new sequence order
    redsys_reference = Sequence.get_id(gateway.redsys_sequence.id)

    currency = None
    if getattr(r, 'currency'):
        currency = getattr(r, 'currency')

    # save transaction draft
    gtransaction = GatewayTransaction()
    gtransaction.description = reference
    gtransaction.origin = origin
    gtransaction.gateway = gateway
    gtransaction.reference_gateway = redsys_reference
    gtransaction.party = session.get('customer', None)
    gtransaction.amount = amount
    if currency:
        gtransaction.currency = currency
    gtransaction.save()

    merchant_code = gateway.redsys_merchant_code
    merchant_secret_key = gateway.redsys_secret_key

    # render redsys data form
    values = {
        'Ds_Merchant_Amount': amount,
        'Ds_Merchant_Currency': gateway.redsys_currency,
        'Ds_Merchant_Order': redsys_reference,
        'Ds_Merchant_ProductDescription': reference,
        'Ds_Merchant_Titular': gateway.redsys_merchant_name,
        'Ds_Merchant_MerchantCode': merchant_code,
        'Ds_Merchant_MerchantURL': url_ipn,
        'Ds_Merchant_UrlOK': url_confirm,
        'Ds_Merchant_UrlKO': url_cancel,
        'Ds_Merchant_MerchantName': gateway.redsys_merchant_name,
        'Ds_Merchant_Terminal': gateway.redsys_terminal,
        'Ds_Merchant_TransactionType': gateway.redsys_transaction_type,
    }
    redsyspayment = Client(business_code=merchant_code, priv_key=merchant_secret_key, sandbox=sandbox)
    redsys_form = redsyspayment.get_pay_form_data(values)

    session['redsys_reference'] = reference

    return render_template('redsys.html',
            redsys_form=redsys_form,
            )
