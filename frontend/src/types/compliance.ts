// India compliance — GST returns (GSTR-1 / GSTR-3B). Money fields are serialised
// as strings (Decimal). Mirrors backend app/schemas/compliance.py.

export interface Gstr1Invoice {
  invoice_id: string;
  name: string;
  posting_date: string;
  counterparty_gstin: string | null;
  invoice_value: string;
  place_of_supply: string;
  reverse_charge: boolean;
  rate: string;
  taxable_value: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
  is_return: boolean;
}

export interface Gstr1B2B {
  gstin: string;
  party_name: string | null;
  invoices: Gstr1Invoice[];
  taxable_value: string;
  total_tax: string;
}

export interface Gstr1B2CS {
  place_of_supply: string;
  supply_type: string;
  rate: string;
  taxable_value: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
}

export interface Gstr1Hsn {
  hsn_code: string | null;
  description: string | null;
  uqc: string | null;
  qty: string;
  rate: string;
  taxable_value: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
}

export interface Gstr1DocSummary {
  nature: string;
  from_no: string;
  to_no: string;
  total_count: number;
  cancelled: number;
  net_issued: number;
}

export interface Gstr1Totals {
  taxable_value: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
  invoice_count: number;
}

export interface Gstr1Report {
  gstin: string | null;
  filing_period: string;
  from_date: string;
  to_date: string;
  b2b: Gstr1B2B[];
  b2cl: Gstr1Invoice[];
  b2cs: Gstr1B2CS[];
  cdnr: Gstr1Invoice[];
  cdnur: Gstr1Invoice[];
  hsn: Gstr1Hsn[];
  docs: Gstr1DocSummary[];
  totals: Gstr1Totals;
}

export interface Gstr3bTaxRow {
  label: string;
  taxable_value: string;
  igst: string;
  cgst: string;
  sgst: string;
  cess: string;
}

export interface Gstr3bInterStateRow {
  place_of_supply: string;
  taxable_value: string;
  igst: string;
}

export interface Gstr3bItcRow {
  label: string;
  igst: string;
  cgst: string;
  sgst: string;
  cess: string;
}

export interface Gstr3bReport {
  gstin: string | null;
  filing_period: string;
  from_date: string;
  to_date: string;
  outward: Gstr3bTaxRow[];
  inter_state_unreg: Gstr3bInterStateRow[];
  itc: Gstr3bItcRow[];
  net_tax_payable: Gstr3bTaxRow;
}
