# -*- coding: utf-8 -*-
from openerp import osv, models, fields, api, _
from openerp.exceptions import except_orm, Warning, RedirectWarning
import openerp.addons.decimal_precision as dp

class account_invoice_line(models.Model):
    """
    En argentina como no se diferencian los impuestos en las facturas, excepto el IVA,
    agrego campos que ignoran el iva solamenta a la hora de imprimir los valores.
    """

    _inherit = "account.invoice.line"
    
    @api.one
    @api.depends('price_unit','quantity','discount','invoice_line_tax_id')
    def _printed_prices(self):
        tax_obj = self.env['account.tax']
        cur_obj = self.env['res.currency']

        _round = (lambda x: cur_obj.round(self.invoice_id.currency_id, x)) if self.invoice_id else (lambda x: x)
        quantity = self.quantity
        discount = self.discount
        printed_price_unit = self.price_unit
        printed_price_net = self.price_unit * (1-(discount or 0.0)/100.0)
        printed_price_subtotal = printed_price_net * quantity

        afip_document_class_id = self.invoice_id.journal_document_class_id.afip_document_class_id
        if afip_document_class_id and not afip_document_class_id.vat_discriminated:
            vat_taxes = [x for x in self.invoice_line_tax_id if x.tax_code_id.vat_tax]
            taxes = tax_obj.compute_all(
                                        vat_taxes, printed_price_net, 1,
                                        product=self.product_id,
                                        partner=self.invoice_id.partner_id)
            printed_price_unit = _round(taxes['total_included'] * (1+(discount or 0.0)/100.0))
            printed_price_net = _round(taxes['total_included'])
            printed_price_subtotal = _round(taxes['total_included'] * quantity)
        
        self.printed_price_unit = printed_price_unit
        self.printed_price_net = printed_price_net
        self.printed_price_subtotal = printed_price_subtotal

    printed_price_unit = fields.Float(
        compute='_printed_prices', 
        digits_compute=dp.get_precision('Account'), 
        string='Unit Price',)
    printed_price_net = fields.Float(
        compute='_printed_prices', 
        digits_compute=dp.get_precision('Account'), 
        string='Net Price',)
    printed_price_subtotal = fields.Float(
        compute='_printed_prices', 
        digits_compute=dp.get_precision('Account'), 
        string='Subtotal',)

class account_invoice(models.Model):
    _inherit = "account.invoice"

    @api.one
    @api.depends('journal_id')
    def _get_available_document_letters(self):
        journal_type = self.get_journal_type(self.type)
        letter_ids = []
        self.available_document_letter_ids = self.env['afip.document_letter']
        if self.use_documents:
            letter_ids = self.get_valid_document_letters(self.partner_id.id, journal_type, self.company_id.id)
            print 'letter_ids', letter_ids
        self.available_document_letter_ids = letter_ids

    @api.one
    @api.depends('amount_untaxed','amount_total','tax_line','journal_document_class_id')
    def _printed_prices(self):
        printed_amount_untaxed = self.amount_untaxed
        printed_tax_ids = [x.id for x in self.tax_line]

        afip_document_class_id = self.journal_document_class_id.afip_document_class_id
        if afip_document_class_id and not afip_document_class_id.vat_discriminated:
            printed_amount_untaxed = sum(line.printed_price_subtotal for line in self.invoice_line)
            printed_tax_ids = [x.id for x in self.tax_line if not x.tax_code_id.vat_tax]
        self.printed_amount_untaxed = printed_amount_untaxed
        self.printed_tax_ids = printed_tax_ids
        self.printed_amount_tax = self.amount_total - printed_amount_untaxed

    @api.multi
    def name_get(self):
        TYPES = {
            'out_invoice': _('Invoice'),
            'in_invoice': _('Supplier Invoice'),
            'out_refund': _('Refund'),
            'in_refund': _('Supplier Refund'),
        }
        result = []
        for inv in self:
            # result.append((inv.id, "%s %s" % (inv.number or TYPES[inv.type], inv.name or '')))
            result.append((inv.id, "%s %s" % (inv.reference or TYPES[inv.type], inv.number or '')))
        return result

    @api.model
    def name_search(self, name, args=None, operator='ilike', limit=100):
        args = args or []
        recs = self.browse()
        if name:
            recs = self.search([('number', '=', name)] + args, limit=limit)
        if not recs:
            # recs = self.search([('name', operator, name)] + args, limit=limit)
            recs = self.search([('reference', operator, name)] + args, limit=limit)
        return recs.name_get()

    available_document_letter_ids = fields.Many2many(
        'afip.document_letter', 
        compute='_get_available_document_letters', 
        string='Available Document Letters')
    printed_amount_tax = fields.Float(
        compute='_printed_prices', 
        digits_compute=dp.get_precision('Account'), 
        string='Tax',)
    printed_amount_untaxed = fields.Float(
        compute='_printed_prices', 
        digits_compute=dp.get_precision('Account'), 
        string='Subtotal',)
    printed_tax_ids = fields.One2many(
        'account.invoice.tax', 
        compute='_printed_prices', 
        string='Tax',)    
    supplier_invoice_number = fields.Char(
        copy=False)
    journal_document_class_id = fields.Many2one(
        'account.journal.afip_document_class', 
        'Documents Type', 
        readonly=True, 
        states={'draft':[('readonly',False)]})
    afip_document_class_id = fields.Many2one(
        'afip.document_class', 
        related='journal_document_class_id.afip_document_class_id',
        string='Document Type', 
        readonly=True, 
        store=True)
    next_invoice_number = fields.Integer(
        related='journal_document_class_id.sequence_id.number_next_actual', 
        string='Next Document Number', 
        readonly=True)
    use_documents = fields.Boolean(
        related='journal_id.use_documents',
        string='Use Documents?', 
        readonly=True)

    @api.one
    @api.constrains('supplier_invoice_number','partner_id','company_id')
    def _check_reference(self):
        if self.type in ['out_invoice','out_refund'] and self.reference and self.state == 'open':
            domain = [('type','in',('out_invoice','out_refund')),
                ('reference','=',self.reference),
                # ('document_number','=',invoice.document_number),
                ('journal_document_class_id.afip_document_class_id','=',self.journal_document_class_id.afip_document_class_id.id),
                ('company_id','=',self.company_id.id),
                ('id','!=',self.id)]
            invoice_ids = self.search(domain)
            if invoice_ids:
                raise Warning(_('Supplier Invoice Number must be unique per Supplier and Company!'))

    _sql_constraints = [
        ('number_supplier_invoice_number', 'unique(supplier_invoice_number, partner_id, company_id)', 'Supplier Invoice Number must be unique per Supplier and Company!'),
    ]

    def create(self, cr, uid, vals, context=None):
        '''We modify create for 2 popuses:
        - Modify create function so it can try to set a right document for the invoice
        - If reference in values, we clean it for argentinian companies as it will be used for invoice number
        ''' 
        # First purpose
        if not context:
            context = {}
        partner_id = vals.get('partner_id', False)
        journal_id = vals.get('journal_id', False)
        if journal_id and partner_id:
            journal = self.pool['account.journal'].browse(cr, uid, int(journal_id), context=context)
            if journal.use_documents:
                journal_type = journal.type
                letter_ids = self.get_valid_document_letters(cr, uid, int(partner_id), journal_type, company_id=journal.company_id.id)
                domain = ['|',('afip_document_class_id.document_letter_id','=',False),('afip_document_class_id.document_letter_id','in',letter_ids),('journal_id','=',journal_id)]
                journal_document_class_ids = self.pool['account.journal.afip_document_class'].search(cr, uid, domain)
                if journal_document_class_ids:
                    vals['journal_document_class_id'] = journal_document_class_ids[0]

                # second purpose
                if 'reference' in vals:
                    vals['reference'] = False

        return super(account_invoice, self).create(cr, uid, vals, context=context)

    @api.multi
    def action_move_create(self):
        obj_sequence = self.env['ir.sequence']
        
        # We write reference field with next invoice number by document type
        for obj_inv in self:
            invtype = obj_inv.type
            # if we have a journal_document_class_id is beacuse we are in a company that use this function
            # also if it has a reference number we use it (for example when cancelling for modification)
            if obj_inv.journal_document_class_id and not obj_inv.reference:
                if invtype in ('out_invoice', 'out_refund'):
                    if not obj_inv.journal_document_class_id.sequence_id:
                        raise osv.except_osv(_('Error!'), _('Please define sequence on the journal related documents to this invoice.'))
                    reference = obj_sequence.next_by_id(obj_inv.journal_document_class_id.sequence_id.id)
                elif invtype in ('in_invoice', 'in_refund'):
                    reference = obj_inv.supplier_invoice_number
                obj_inv.write({'reference':reference})
        res = super(account_invoice, self).action_move_create()
        
        # on created moves we write the document type
        for obj_inv in self:
            invtype = obj_inv.type
            # if we have a journal_document_class_id is beacuse we are in a company that use this function
            if obj_inv.journal_document_class_id:
                obj_inv.move_id.write({'document_class_id':obj_inv.journal_document_class_id.afip_document_class_id.id})
        return res
    
    def get_journal_type(self, cr, uid, invoice_type, context=None):
        if invoice_type == 'in_invoice':
            journal_type = 'purchase'
        elif invoice_type == 'in_refund':
            journal_type = 'purchase_refund'
        elif invoice_type == 'out_invoice':
            journal_type = 'sale'
        elif invoice_type == 'out_refund':
            journal_type = 'sale_refund'
        else:
            journal_type = False
        return journal_type

    def get_valid_document_letters(self, cr, uid, partner_id, journal_type, company_id=False, context=None):
        if context is None:
            context = {}

        document_letter_obj = self.pool.get('afip.document_letter')
        user = self.pool.get('res.users').browse(cr, uid, uid, context=context)
        partner = self.pool.get('res.partner').browse(cr, uid, partner_id, context=context)

        if not partner_id or not company_id or not journal_type:
            return []
            
        partner = partner.commercial_partner_id

        if company_id == False:
            company_id = context.get('company_id', user.company_id.id) 
        company = self.pool.get('res.company').browse(cr, uid, company_id, context)

        if journal_type in ['sale','sale_refund']:
            issuer_responsability_id = company.partner_id.responsability_id.id
            receptor_responsability_id = partner.responsability_id.id 
        elif journal_type in ['purchase','purchase_refund']:
            issuer_responsability_id = partner.responsability_id.id    
            receptor_responsability_id = company.partner_id.responsability_id.id         
        else:
            raise except_orm(_('Journal Type Error'),
                    _('Journal Type Not defined)'))

        if not company.partner_id.responsability_id.id:
            raise except_orm(_('Your company has not setted any responsability'),
                    _('Please, set your company responsability in the company partner before continue.'))            

        document_letter_ids = document_letter_obj.search(cr, uid, [('issuer_ids', 'in', issuer_responsability_id),('receptor_ids', 'in', receptor_responsability_id)], context=context)
        return document_letter_ids          

    def on_change_journal_document_class_id(self, cr, uid, ids, journal_document_class_id):     
        result = {}
        next_invoice_number = False
        if journal_document_class_id:
            journal_document_class = self.pool['account.journal.afip_document_class'].browse(cr, uid, journal_document_class_id)
            if journal_document_class.sequence_id:
                next_invoice_number = journal_document_class.sequence_id.number_next
        
        result['value'] = {'next_invoice_number':next_invoice_number}
        return result
    
    def onchange_partner_id(self, cr, uid, ids, type, partner_id,
                            date_invoice=False, payment_term=False,
                            partner_bank_id=False, company_id=False, journal_id=False, context=None):
        result = super(account_invoice,self).onchange_partner_id(cr, uid, ids,
                       type, partner_id, date_invoice, payment_term,
                       partner_bank_id, company_id, context=context)
        if 'value' not in result: result['value'] = {}                
        journal_document_class_id = False
        if journal_id and partner_id:
            journal_type = self.get_journal_type(cr, uid, type)
            letter_ids = self.get_valid_document_letters(cr, uid, partner_id, journal_type, company_id=company_id)
            domain = ['|',('afip_document_class_id.document_letter_id','=',False),('afip_document_class_id.document_letter_id','in',letter_ids),('journal_id','=',journal_id)]
            journal_document_class_ids = self.pool['account.journal.afip_document_class'].search(cr, uid, domain)
            if journal_document_class_ids:
                journal_document_class_id = journal_document_class_ids[0]
            if 'domain' not in result: result['domain'] = {}          
            result['domain']['journal_document_class_id'] = [('id', 'in', journal_document_class_ids)]
        if 'value' not in result: result['value'] = {}          
        result['value']['journal_document_class_id'] = journal_document_class_id
        return result

    def onchange_journal_id(self, cr, uid, ids, journal_id=False, partner_id=False, context=None):
        result = super(account_invoice, self).onchange_journal_id(cr, uid, ids, journal_id, context)
        journal_document_class_id = False        
        if journal_id:
            journal = self.pool['account.journal'].browse(cr, uid, journal_id)
            result['value']['use_documents'] = journal.use_documents
            if partner_id:
                journal_type = journal.type
                letter_ids = self.get_valid_document_letters(cr, uid, partner_id, journal_type, company_id=journal.company_id.id)
                domain = ['|',('afip_document_class_id.document_letter_id','=',False),('afip_document_class_id.document_letter_id','in',letter_ids),('journal_id','=',journal_id)]
                journal_document_class_ids = self.pool['account.journal.afip_document_class'].search(cr, uid, domain)
                if journal_document_class_ids:
                    journal_document_class_id = journal_document_class_ids[0]
                if 'domain' not in result: result['domain'] = {}        
                result['domain']['journal_document_class_id'] = [('id', 'in', journal_document_class_ids)]  
        if 'value' not in result: result['value'] = {}          
        result['value']['journal_document_class_id'] = journal_document_class_id
        return result