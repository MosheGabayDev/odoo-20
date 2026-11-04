"""Finite business surface. Never expose the technical ORM or identity stores."""
TOOLS=frozenset(('search_count','search_read','read_records','read_group',
    'create_records','update_records','delete_records','call_method','list_models',
    'describe_model','whoami','get_access_rights','get_messages','post_message',
    'schedule_activity','read_resource','authorize_download','export_records',
    'print_report','authorize_upload','upload_file','system_info','list_modules','list_languages'))
BASE={'id','name','display_name','active','company_id','create_date','write_date'}
FIELDS={
 'project.project':'description user_id partner_id privacy_visibility date_start date type_ids',
 'project.task':'description project_id stage_id state priority user_ids partner_id date_deadline parent_id tag_ids sequence planned_hours allocated_hours',
 'project.task.type':'sequence fold project_ids user_id',
 'project.tags':'color',
 'crm.lead':'description type partner_id contact_name email_from phone mobile user_id team_id stage_id priority probability expected_revenue date_deadline tag_ids lost_reason_id source_id medium_id campaign_id',
 'crm.stage':'sequence fold is_won team_id requirements',
 'crm.team':'user_id',
 'crm.tag':'color',
 'crm.lost.reason':'',
 'res.partner':'is_company parent_id type street street2 city zip country_id state_id email phone website vat category_id comment customer_rank supplier_rank',
 'res.partner.category':'color parent_id',
 'res.users':'',
 'res.company':'currency_id country_id',
 'res.country':'code',
 'res.country.state':'code country_id',
 'res.currency':'symbol rounding decimal_places',
 'res.lang':'code',
 'product.template':'description description_sale description_purchase list_price standard_price default_code barcode categ_id uom_id uom_po_id type is_storable sale_ok purchase_ok taxes_id supplier_taxes_id image_1920',
 'product.product':'product_tmpl_id description_sale list_price standard_price default_code barcode categ_id uom_id type sale_ok purchase_ok image_1920',
 'product.category':'parent_id',
 'product.pricelist':'currency_id discount_policy',
 'uom.uom':'rounding factor factor_inv',
 'sale.order':'partner_id partner_invoice_id partner_shipping_id user_id team_id pricelist_id currency_id date_order validity_date state order_line amount_untaxed amount_tax amount_total note client_order_ref payment_term_id project_id',
 'sale.order.line':'order_id product_id product_uom_qty product_uom_id product_uom price_unit discount tax_ids sequence display_type price_subtotal price_tax price_total',
 'account.move':'move_type partner_id invoice_date invoice_date_due date journal_id currency_id state invoice_line_ids line_ids amount_untaxed amount_tax amount_total amount_residual ref payment_reference invoice_payment_term_id',
 'account.move.line':'move_id account_id partner_id product_id quantity price_unit discount tax_ids debit credit balance amount_currency currency_id date date_maturity display_type',
 'account.journal':'code type currency_id default_account_id',
 'account.account':'code account_type reconcile currency_id company_ids',
 'account.tax':'amount amount_type type_tax_use price_include sequence tax_group_id',
 'account.tax.group':'sequence',
 'account.payment.term':'note',
 'account.analytic.account':'code partner_id plan_id',
 'account.analytic.plan':'parent_id',
 'calendar.event':'description start stop allday duration user_id partner_ids privacy location videocall_location',
 'mail.activity':'res_model res_id activity_type_id user_id summary note date_deadline',
 'mail.activity.type':'category delay_count delay_unit',
 'ir.attachment':'mimetype file_size res_model res_id',
 'utm.source':'', 'utm.medium':'', 'utm.campaign':'user_id',
}
READ={m:BASE|set(fields.split()) for m,fields in FIELDS.items()}
READ['res.users']={'id','name','display_name','active','company_id'}
READ['res.company']={'id','name','display_name','currency_id','country_id'}
WRITE={m:set(fields.split())|{'name','active'} for m,fields in FIELDS.items()}
READ_ONLY=frozenset(('res.users','res.company','res.country','res.country.state','res.currency','res.lang',
    'uom.uom','mail.activity.type','mail.activity','ir.attachment'))
for model in READ_ONLY:WRITE.pop(model,None)
WRITE['ir.attachment']={'name'}
WRITE['mail.activity']={'summary','note','date_deadline','user_id','activity_type_id'}
for model in WRITE:
 WRITE[model]-={'create_date','write_date','amount_total','amount_tax','amount_untaxed','amount_residual',
     'price_subtotal','price_tax','price_total','debit','credit','balance','amount_currency','customer_rank','supplier_rank'}
WRITE['account.move']-= {'state','line_ids'}
WRITE['sale.order']-= {'state'}
WRITE['project.task.type']-= {'user_id'}
WRITE['calendar.event']-= {'videocall_location'}
GLOBAL=frozenset(('project.tags','crm.stage','crm.tag','crm.lost.reason','res.partner.category','product.category',
    'account.tax.group','account.analytic.plan','utm.source','utm.medium','utm.campaign'))
METHODS={
 'crm.lead':{'action_set_won','action_set_lost','action_archive','action_unarchive'},
 'project.task':{'action_archive','action_unarchive','action_assign_to_me'},
 'project.project':{'action_archive','action_unarchive'},
 'sale.order':{'action_confirm','action_cancel','action_draft'},
 'account.move':{'action_post','button_draft','button_cancel'},
 'product.template':{'action_archive','action_unarchive'},
 'product.product':{'action_archive','action_unarchive'},
 'res.partner':{'action_archive','action_unarchive'},
 'mail.activity':{'action_done','action_feedback'},
}
MAX_RECORDS=100
MAX_FILE_BYTES=5*1024*1024
