#This file is part redsysgateway blueprint for Flask.
#The COPYRIGHT file at the top level of this repository contains
#the full copyright notices and license terms.
from flask import (Blueprint, request, render_template, flash, current_app, g,
    session, abort, url_for, redirect)
from flask_babel import gettext as _
from galatea.tryton import tryton
from galatea.csrf import csrf
from decimal import Decimal, InvalidOperation
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

    if not gateway:
        abort(404)

    sandbox = current_app.config['DEBUG']
    merchant_code = gateway.redsys_merchant_code
    merchant_secret_key = gateway.redsys_secret_key

    merchant_parameters = request.form['Ds_MerchantParameters']
    signature = request.form['Ds_Signature']

    redsyspayment = Client(business_code=merchant_code,
        secret_key=merchant_secret_key, sandbox=sandbox)

    valid_signature = redsyspayment.redsys_check_response(
        signature.encode('utf-8'), merchant_parameters.encode('utf-8'))
    if not valid_signature:
        abort(500)

    merchant_parameters = redsyspayment.decode_parameters(merchant_parameters)

    reference = merchant_parameters.get('Ds_Order')
    authorisation_code = merchant_parameters.get('Ds_AuthorisationCode')
    amount = merchant_parameters.get('Ds_Amount', 0)
    response = merchant_parameters.get('Ds_Response')


    log = "\n".join([('%s: %s' % (k, v)) for k, v in
            merchant_parameters.items()])

    # Search transaction
    gtransactions = GatewayTransaction.search([
            ('reference_gateway', '=', reference),
            ('state', '=', 'draft'),
            ], limit=1)
    if gtransactions:
        gtransaction, = gtransactions
        gtransaction.authorisation_code = authorisation_code
        gtransaction.amount = Decimal(amount)/100
        gtransaction.log = log
        gtransaction.save()
    else:
        gtransaction = GatewayTransaction()
        gtransaction.description = reference
        gtransaction.authorisation_code = authorisation_code
        gtransaction.gateway = gateway
        gtransaction.reference_gateway = reference
        gtransaction.amount = Decimal(amount)/100
        gtransaction.log = log
        gtransaction.save()

    # Process transaction 0000 - 0099: Done
    if int(response) < 100:
        GatewayTransaction.confirm([gtransaction])
        return response

    # other transactions: cancel
    GatewayTransaction.cancel([gtransaction])
    return response

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
        if payment.payment_type.gateway.method == 'redsys':
            gateway = payment.payment_type.gateway
            break

    if not gateway:
        abort(404)

    url_ipn = '%s%s' % (base_url, url_for('.ipn', lang=g.language))
    url_confirm = '%s%s' % (base_url, url_for('.confirm', lang=g.language))
    url_cancel = '%s%s' % (base_url, url_for('.cancel', lang=g.language))

    origin = request.form.get('origin', None)
    record = None
    if origin:
        try:
            options = origin.split(',')
            record = tryton.pool.get(options[0])(options[1])
        except:
            abort(500)

        # cancel old possible transactions not used
        gtransactions = GatewayTransaction.search([
            ('origin', '=', origin),
            ('state', '=', 'draft'),
            ])
        if gtransactions:
            GatewayTransaction.cancel(gtransactions)

    reference = request.form.get('reference')
    if record:
        if getattr(record, 'total_amount'):
            total_amount = getattr(record, 'total_amount')
        else:
            flash(_("Error when get total amount to pay. Repeat or contact us."),
                "danger")
            redirect(url_for('/', lang=g.language))
        amount = total_amount - record.gateway_amount
        if getattr(record, 'currency'):
            currency = getattr(record, 'currency')
        else:
            currency = shop.currency
    else:
        currency = shop.currency
        if request.form.get('amount'):
            try:
                amount = Decimal(request.form.get('amount'))
            except InvalidOperation:
                abort(500)
        else:
            abort(404)

    # Redsys force to use a new sequence order
    redsys_reference = gateway.redsys_sequence.get()

    # save transaction draft
    gtransaction = GatewayTransaction()
    gtransaction.description = reference
    gtransaction.origin = origin
    gtransaction.gateway = gateway
    gtransaction.reference_gateway = redsys_reference
    gtransaction.party = session.get('customer', None)
    gtransaction.amount = amount
    gtransaction.currency = currency
    gtransaction.save()

    merchant_code = gateway.redsys_merchant_code
    merchant_secret_key = gateway.redsys_secret_key

    # render redsys data form
    values = {
        'DS_MERCHANT_AMOUNT': amount,
        'DS_MERCHANT_CURRENCY': gateway.redsys_currency,
        'DS_MERCHANT_ORDER': redsys_reference,
        'DS_MERCHANT_PRODUCTDESCRIPTION': reference,
        'DS_MERCHANT_TITULAR': gateway.redsys_merchant_name,
        'DS_MERCHANT_MERCHANTCODE': merchant_code,
        'DS_MERCHANT_MERCHANTURL': url_ipn,
        'DS_MERCHANT_URLOK': url_confirm,
        'DS_MERCHANT_URLKO': url_cancel,
        'DS_MERCHANT_MERCHANTNAME': gateway.redsys_merchant_name,
        'DS_MERCHANT_TERMINAL': gateway.redsys_terminal,
        'DS_MERCHANT_TRANSACTIONTYPE': gateway.redsys_transaction_type,
        }
    redsyspayment = Client(business_code=merchant_code,
        secret_key=merchant_secret_key, sandbox=sandbox)
    redsys_form = redsyspayment.redsys_generate_request(values)

    session['redsys_reference'] = reference

    return render_template('redsys.html',
            redsys_form=redsys_form,
            redsys_reference=redsys_reference,
            )
