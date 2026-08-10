// Single source of truth for every user-visible string in the Income Tax module.
//
// `label` is ALWAYS the full business term — never a bare abbreviation. Where a
// chartered accountant expects the section number, it stays inside the label.
// `short` exists only for dense grid headers and must always be paired with an
// InfoTip carrying the full `label`.
//
// Keys are namespaced: head.* character.* stage.* direction.* regime.*
// filingType.* creditKind.* challanType.* lossKind.* majorHead.* minorHead.*
// status.* section.* term.*

export interface TaxTerm {
  label: string;
  short?: string;
  statutoryRef?: string;
  help?: string;
}

export const TAX_TERMS: Record<string, TaxTerm> = {
  // --- Heads of income (section 14) -------------------------------------------
  "head.SALARY": {
    label: "Salaries",
    statutoryRef: "Section 15",
    help: "Income earned as an employee.",
  },
  "head.HP": {
    label: "Income from House Property",
    short: "House Property",
    statutoryRef: "Section 22",
    help: "Rental income from land or buildings, after the standard deduction and interest on borrowed capital.",
  },
  "head.PGBP": {
    label: "Profits and Gains of Business or Profession",
    short: "Business Income",
    statutoryRef: "Section 28",
    help: "Operating profit of the business or profession, before tax adjustments.",
  },
  "head.CG": {
    label: "Capital Gains",
    statutoryRef: "Section 45",
    help: "Gain on transfer of a capital asset, split by holding period into short term and long term.",
  },
  "head.OS": {
    label: "Income from Other Sources",
    short: "Other Sources",
    statutoryRef: "Section 56",
    help: "Interest, dividend, winnings and anything not taxable under another head.",
  },

  // --- Income character (drives the rate schedule) ----------------------------
  "character.ORDINARY": {
    label: "Ordinary Business Income",
    short: "Ordinary",
    help: "Income taxed at the entity's normal slab or flat rate, with no special character rate.",
  },
  "character.LTCG_112A": {
    label: "Long Term Capital Gains on Listed Securities (Section 112A)",
    short: "Long Term Gains (112A)",
    statutoryRef: "Section 112A",
    help: "Long term gains on listed equity and equity-oriented funds, taxed at a concessional rate above the statutory exempt threshold.",
  },
  "character.LTCG_112": {
    label: "Long Term Capital Gains — Other Assets (Section 112)",
    short: "Long Term Gains (112)",
    statutoryRef: "Section 112",
    help: "Long term gains on assets other than listed equity, taxed at the Section 112 rate.",
  },
  "character.STCG_111A": {
    label: "Short Term Capital Gains on Listed Securities (Section 111A)",
    short: "Short Term Gains (111A)",
    statutoryRef: "Section 111A",
    help: "Short term gains on listed equity and equity-oriented funds, taxed at the Section 111A rate.",
  },
  "character.LOTTERY_115BB": {
    label: "Winnings from Lotteries, Crossword Puzzles and Games (Section 115BB)",
    short: "Winnings (115BB)",
    statutoryRef: "Section 115BB",
    help: "Winnings taxed at a flat rate with no deduction and no set-off of losses.",
  },
  "character.VDA_115BBH": {
    label: "Income from Transfer of Virtual Digital Assets (Section 115BBH)",
    short: "Virtual Digital Assets (115BBH)",
    statutoryRef: "Section 115BBH",
    help: "Gains on virtual digital assets, taxed at a flat rate with only the cost of acquisition allowed.",
  },
  "character.DIVIDEND": {
    label: "Dividend Income (Section 8)",
    short: "Dividend",
    statutoryRef: "Section 8",
    help: "Dividend declared, distributed or paid, taxable in the hands of the shareholder.",
  },

  // --- Adjustment stage (where in the computation the adjustment lands) --------
  "stage.PGBP": {
    label: "Business Income",
    short: "Business Income",
    statutoryRef: "Sections 28 to 44",
    help: "Adjustments that change the profit of the business or profession.",
  },
  "stage.ICDS": {
    label: "Income Computation and Disclosure Standards",
    short: "Disclosure Standards",
    statutoryRef: "Section 145(2)",
    help: "Adjustments arising from the notified Income Computation and Disclosure Standards.",
  },
  "stage.ChapterVIA": {
    label: "Chapter VI-A Deductions",
    short: "Chapter VI-A",
    statutoryRef: "Chapter VI-A",
    help: "Deductions from gross total income, such as Sections 80G, 80JJAA and 80M.",
  },
  "stage.SetOff": {
    label: "Loss Set-off",
    short: "Set-off",
    statutoryRef: "Sections 70 to 79",
    help: "Adjustments applied while setting off current-year and brought forward losses.",
  },
  "stage.MAT": {
    label: "Minimum Alternate Tax",
    short: "Minimum Alternate Tax",
    statutoryRef: "Section 115JB",
    help: "Adjustments to book profit for the Minimum Alternate Tax comparison.",
  },
  "stage.Other": {
    label: "Other",
    help: "Adjustments that do not belong to any other stage of the computation.",
  },

  // --- Adjustment direction ---------------------------------------------------
  "direction.Add": {
    label: "Add back (disallowed for tax)",
    short: "Add back",
    help: "An expense claimed in the books that the Income-tax Act does not allow, so it is added back to profit.",
  },
  "direction.Less": {
    label: "Deduct (allowed only for tax)",
    short: "Deduct",
    help: "An allowance the Income-tax Act permits but the books do not carry, so it is deducted from profit.",
  },

  // --- Tax regimes ------------------------------------------------------------
  "regime.Normal": {
    label: "Normal Provisions",
    short: "Normal",
    help: "Tax computed under the regular rates with all incentives and deductions available.",
  },
  "regime.115BAA": {
    label: "Concessional Rate (Section 115BAA)",
    short: "Concessional (115BAA)",
    statutoryRef: "Section 115BAA",
    help: "A domestic company may elect a lower flat rate, forfeiting most incentive deductions and additional depreciation. The election is irrevocable.",
  },
  "regime.115BAB": {
    label: "New Manufacturing Company (Section 115BAB)",
    short: "New Manufacturing (115BAB)",
    statutoryRef: "Section 115BAB",
    help: "A concessional rate for a new domestic manufacturing company incorporated within the statutory window. The election is irrevocable.",
  },
  "regime.115BA": {
    label: "Manufacturing Company Concessional Rate (Section 115BA)",
    short: "Manufacturing (115BA)",
    statutoryRef: "Section 115BA",
    help: "The earlier concessional rate for new manufacturing companies, without additional depreciation or investment allowance.",
  },
  "regime.115BAC": {
    label: "New Personal Tax Regime (Section 115BAC)",
    short: "New Regime (115BAC)",
    statutoryRef: "Section 115BAC",
    help: "The default regime for individuals and Hindu undivided families, with lower slab rates and fewer deductions.",
  },
  "regime.Old": {
    label: "Old Personal Tax Regime",
    short: "Old Regime",
    help: "The pre-existing slab rates for individuals, retaining Chapter VI-A deductions and exemptions.",
  },

  // --- Filing type ------------------------------------------------------------
  "filingType.Original": {
    label: "Original Return (Section 139(1))",
    short: "Original",
    statutoryRef: "Section 139(1)",
    help: "The first return filed for the assessment year, on or before the due date.",
  },
  "filingType.Revised": {
    label: "Revised Return (Section 139(5))",
    short: "Revised",
    statutoryRef: "Section 139(5)",
    help: "Replaces an earlier return to correct an omission or a wrong statement.",
  },
  "filingType.Belated": {
    label: "Belated Return (Section 139(4))",
    short: "Belated",
    statutoryRef: "Section 139(4)",
    help: "A return filed after the due date, attracting interest for late filing.",
  },
  "filingType.Updated": {
    label: "Updated Return (Section 139(8A))",
    short: "Updated",
    statutoryRef: "Section 139(8A)",
    help: "A return updating a previously filed or unfiled year, with additional tax payable.",
  },

  // --- Credit kinds -----------------------------------------------------------
  "creditKind.TDS": {
    label: "Tax Deducted at Source",
    short: "Tax Deducted",
    statutoryRef: "Chapter XVII-B",
    help: "Tax deducted by a payer on your income and deposited against your permanent account number.",
  },
  "creditKind.TCS": {
    label: "Tax Collected at Source",
    short: "Tax Collected",
    statutoryRef: "Section 206C",
    help: "Tax collected by a seller on specified transactions and deposited against your permanent account number.",
  },
  "creditKind.AdvanceTax": {
    label: "Advance Tax",
    statutoryRef: "Section 208",
    help: "Tax paid in instalments during the previous year itself.",
  },
  "creditKind.SelfAssessment": {
    label: "Self-Assessment Tax",
    statutoryRef: "Section 140A",
    help: "Balance tax paid by you at the time of filing the return.",
  },

  // --- Challan type -----------------------------------------------------------
  "challanType.AdvanceTax": {
    label: "Advance Tax",
    statutoryRef: "Section 208",
    help: "A challan paying an advance tax instalment during the previous year.",
  },
  "challanType.SelfAssessment": {
    label: "Self-Assessment Tax",
    statutoryRef: "Section 140A",
    help: "A challan paying the balance tax due with the return.",
  },
  "challanType.RegularAssessment": {
    label: "Tax on Regular Assessment",
    short: "Regular Assessment",
    statutoryRef: "Section 143(3)",
    help: "A challan paying a demand raised on assessment.",
  },

  // --- Loss kinds -------------------------------------------------------------
  "lossKind.Business": {
    label: "Business Loss",
    statutoryRef: "Section 72",
    help: "A loss of the business or profession, which may be carried forward for eight assessment years.",
  },
  "lossKind.UnabsorbedDep": {
    label: "Unabsorbed Depreciation",
    short: "Unabsorbed Depreciation",
    statutoryRef: "Section 32(2)",
    help: "Depreciation that could not be absorbed against income, carried forward indefinitely.",
  },
  "lossKind.Speculation": {
    label: "Speculation Business Loss",
    short: "Speculation Loss",
    statutoryRef: "Section 73",
    help: "A loss of a speculation business, which may be set off only against speculation profit.",
  },
  "lossKind.STCG": {
    label: "Short Term Capital Loss",
    statutoryRef: "Section 74",
    help: "A short term capital loss, which may be set off against either short term or long term capital gains.",
  },
  "lossKind.LTCG": {
    label: "Long Term Capital Loss",
    statutoryRef: "Section 74",
    help: "A long term capital loss, which may be set off only against long term capital gains.",
  },
  "lossKind.OS": {
    label: "Loss from Other Sources",
    short: "Other Sources Loss",
    statutoryRef: "Section 74A",
    help: "A loss under the head income from other sources, subject to statutory restrictions.",
  },

  // --- Challan heads ----------------------------------------------------------
  "majorHead.0020": {
    label: "Corporation Tax (Companies)",
    short: "Corporation Tax",
    statutoryRef: "Major Head 0020",
    help: "Use this head for tax deposited by a company.",
  },
  "majorHead.0021": {
    label: "Income Tax (Other than Companies)",
    short: "Income Tax (Non-company)",
    statutoryRef: "Major Head 0021",
    help: "Use this head for tax deposited by an assessee other than a company.",
  },
  "minorHead.100": {
    label: "Advance Tax",
    statutoryRef: "Minor Head 100",
    help: "A deposit towards an advance tax instalment.",
  },
  "minorHead.300": {
    label: "Self-Assessment Tax",
    statutoryRef: "Minor Head 300",
    help: "A deposit of the balance tax paid with the return.",
  },
  "minorHead.400": {
    label: "Tax on Regular Assessment",
    short: "Regular Assessment",
    statutoryRef: "Minor Head 400",
    help: "A deposit against a demand raised on assessment.",
  },

  // --- Document status + section status ---------------------------------------
  "status.0": { label: "Draft", help: "Still editable; nothing has been posted." },
  "status.1": { label: "Submitted", help: "Locked and posted; reverse it by cancelling." },
  "status.2": { label: "Cancelled", help: "Reversed; kept for the audit trail." },
  "status.not-started": { label: "Not started", help: "Nothing has been entered in this section yet." },
  "status.in-progress": { label: "In progress", help: "Partly entered — some expected data is still missing." },
  "status.complete": { label: "Complete", help: "Everything this section needs is present." },
  "status.has-errors": { label: "Needs attention", help: "This section has an issue that must be resolved before filing." },

  // --- Workspace sections -----------------------------------------------------
  "section.overview": {
    label: "Setup & Basis",
    short: "Setup",
    help: "Choose the assessment year, entity class, regime election, filing type and audit applicability, then start from a template, the previous year or the books.",
  },
  "section.income": {
    label: "Statement of Income",
    short: "Income",
    help: "Enter income head-wise; the net figure is derived from gross less deductions.",
  },
  "section.adjustments": {
    label: "Tax Adjustments",
    short: "Adjustments",
    help: "Add back amounts the Income-tax Act disallows and deduct allowances the books do not carry.",
  },
  "section.depreciation": {
    label: "Depreciation (Income-tax Act)",
    short: "Depreciation",
    help: "Block-wise written down value register; rate, allowable depreciation and closing value are all derived.",
  },
  "section.losses": {
    label: "Brought Forward Losses & Set-off",
    short: "Losses",
    help: "Maintain the loss ledger with expiry years and review the set-off the last computation actually applied.",
  },
  "section.mat": {
    label: "Minimum Alternate Tax (Section 115JB)",
    short: "Minimum Alternate Tax",
    statutoryRef: "Section 115JB",
    help: "Compare tax on book profit with tax under the normal provisions and track the Section 115JAA credit.",
  },
  "section.credits": {
    label: "Taxes Already Paid",
    short: "Taxes Paid",
    help: "Record tax deducted at source, tax collected at source and advance tax already credited to you.",
  },
  "section.challans": {
    label: "Tax Payment Challans",
    short: "Challans",
    help: "Record challans; submitting one posts the payment to the general ledger.",
  },
  "section.reconciliation": {
    label: "Reconcile with Form 26AS",
    short: "Reconcile",
    help: "Upload the annual tax statement from the portal, match it against your books and adopt the portal amounts.",
  },
  "section.interest": {
    label: "Advance Tax & Interest",
    short: "Interest",
    statutoryRef: "Sections 234A, 234B and 234C",
    help: "Review the instalment schedule, any shortfall and the interest that follows from it.",
  },
  "section.summary": {
    label: "Statement of Total Income",
    short: "Summary",
    help: "The reviewer's statement, with variances against the previous year and against the books.",
  },
  "section.review": {
    label: "Review & Validate",
    short: "Review",
    help: "Blocking errors and advisory warnings, each one a click away from the field that caused it.",
  },
  "section.filing": {
    label: "Return Filing (ITR-6)",
    short: "Filing",
    statutoryRef: "Section 139",
    help: "Generate the return, record the acknowledgement and chain a revised, belated or updated return.",
  },
  "section.audit": {
    label: "Computation History",
    short: "History",
    help: "Every persisted computation run, with its fingerprints and explanation, in append-only order.",
  },

  // --- Concepts ---------------------------------------------------------------
  "term.assessmentYear": {
    label: "Assessment Year",
    statutoryRef: "Section 2(9)",
    help: "The year in which the income of the previous year is assessed to tax.",
  },
  "term.previousYear": {
    label: "Previous Year",
    statutoryRef: "Section 3",
    help: "The financial year whose income is being computed.",
  },
  "term.financialYear": {
    label: "Financial Year",
    help: "The accounting year from 1 April to 31 March.",
  },
  "term.pan": {
    label: "Permanent Account Number",
    short: "Permanent Account Number",
    statutoryRef: "Section 139A",
    help: "The ten-character identifier issued by the Income Tax Department.",
  },
  "term.tan": {
    label: "Tax Deduction and Collection Account Number",
    short: "Tax Deduction Account Number",
    statutoryRef: "Section 203A",
    help: "The ten-character number quoted by a person who deducts or collects tax at source.",
  },
  "term.cin": {
    label: "Corporate Identity Number",
    help: "The registration number issued by the Registrar of Companies.",
  },
  "term.entityClass": {
    label: "Entity Class",
    help: "The class of assessee — company, firm, individual and so on — which selects the rate schedule.",
  },
  "term.residentialStatus": {
    label: "Residential Status",
    statutoryRef: "Section 6",
    help: "Resident, non-resident or resident but not ordinarily resident; it decides the scope of taxable income.",
  },
  "term.regime": {
    label: "Tax Regime",
    help: "The rate regime elected for the year, which fixes the rates and the incentives available.",
  },
  "term.grossTotalIncome": {
    label: "Gross Total Income",
    statutoryRef: "Section 80B(5)",
    help: "Total of income under all heads, after set-off of losses but before Chapter VI-A deductions.",
  },
  "term.totalIncome": {
    label: "Total Income",
    statutoryRef: "Section 2(45)",
    help: "Gross total income less Chapter VI-A deductions — the figure tax is charged on.",
  },
  "term.bookProfit": {
    label: "Book Profit",
    statutoryRef: "Section 115JB",
    help: "Profit as per the statement of profit and loss, adjusted as Section 115JB requires.",
  },
  "term.taxDepreciation": {
    label: "Depreciation under the Income-tax Act",
    short: "Tax Depreciation",
    statutoryRef: "Section 32",
    help: "Depreciation computed block-wise on written down value, which replaces the depreciation charged in the books.",
  },
  "term.additionalDepreciation": {
    label: "Additional Depreciation on New Plant and Machinery",
    short: "Additional Depreciation",
    statutoryRef: "Section 32(1)(iia)",
    help: "An extra allowance on eligible new plant and machinery. A concessional regime election forfeits it.",
  },
  "term.writtenDownValue": {
    label: "Written Down Value",
    short: "Written Down Value",
    statutoryRef: "Section 43(6)",
    help: "The tax value of a block of assets, carried forward year on year.",
  },
  "term.openingWrittenDownValue": {
    label: "Opening Written Down Value",
    short: "Opening Value",
    statutoryRef: "Section 43(6)",
    help: "The block's written down value at the start of the year, normally last year's closing value.",
  },
  "term.closingWrittenDownValue": {
    label: "Closing Written Down Value",
    short: "Closing Value",
    statutoryRef: "Section 43(6)",
    help: "Opening value plus additions less deletions and depreciation allowed for the year.",
  },
  "term.halfRateRule": {
    label: "Half-rate rule for assets used less than 180 days",
    short: "Half-rate additions",
    statutoryRef: "Section 32",
    help: "An asset put to use for less than 180 days in the year gets half the normal rate of depreciation.",
  },
  "term.depreciationBlock": {
    label: "Block of Assets",
    short: "Block",
    statutoryRef: "Section 2(11)",
    help: "A group of assets of the same class carrying the same rate of depreciation.",
  },
  "term.broughtForwardLoss": {
    label: "Brought Forward Loss",
    short: "Brought Forward",
    statutoryRef: "Sections 72 to 74A",
    help: "A loss of an earlier year available for set-off against this year's income.",
  },
  "term.setOff": {
    label: "Set-off of Losses",
    short: "Set-off",
    statutoryRef: "Sections 70 to 79",
    help: "The statutory order in which losses are absorbed against income of the year.",
  },
  "term.lossExpiry": {
    label: "Last Assessment Year for Set-off",
    short: "Expires After",
    help: "The final assessment year in which this loss may still be set off.",
  },
  "term.minimumAlternateTax": {
    label: "Minimum Alternate Tax (Section 115JB)",
    short: "Minimum Alternate Tax",
    statutoryRef: "Section 115JB",
    help: "A floor tax on book profit; a company pays the higher of this and tax under the normal provisions.",
  },
  "term.minimumAlternateTaxCredit": {
    label: "Minimum Alternate Tax Credit (Section 115JAA)",
    short: "Alternate Tax Credit",
    statutoryRef: "Section 115JAA",
    help: "Credit for the excess of Minimum Alternate Tax over normal tax, usable in a later year when normal tax is higher.",
  },
  "term.taxDeductedAtSource": {
    label: "Tax Deducted at Source",
    short: "Tax Deducted",
    statutoryRef: "Chapter XVII-B",
    help: "Tax withheld by a payer and deposited against your permanent account number.",
  },
  "term.taxCollectedAtSource": {
    label: "Tax Collected at Source",
    short: "Tax Collected",
    statutoryRef: "Section 206C",
    help: "Tax collected by a seller on specified transactions and deposited against your permanent account number.",
  },
  "term.form26as": {
    label: "Form 26AS (Annual Tax Statement)",
    short: "Form 26AS",
    statutoryRef: "Rule 31AB",
    help: "The department's consolidated statement of tax credited against your permanent account number.",
  },
  "term.reconcile26as": {
    label: "Reconcile with Form 26AS",
    short: "Reconcile",
    help: "Match the credits in your books against the portal statement and settle the differences.",
  },
  "term.bankBranchCode": {
    label: "Bank Branch Code (BSR)",
    short: "Bank Branch Code",
    help: "The seven-digit basic statistical return code of the bank branch that accepted the challan.",
  },
  "term.challanSerial": {
    label: "Challan Serial Number",
    short: "Challan Serial",
    help: "The serial number the bank allotted to the challan on the date of deposit.",
  },
  "term.challanIdentificationNumber": {
    label: "Challan Identification Number (CIN)",
    short: "Challan Identification",
    help: "Bank branch code, date of deposit and challan serial number taken together — the unique key of a payment.",
  },
  "term.majorHead": {
    label: "Major Head",
    help: "Whether the deposit is corporation tax or income tax other than companies.",
  },
  "term.minorHead": {
    label: "Minor Head",
    help: "What the deposit is for — advance tax, self-assessment tax or tax on regular assessment.",
  },
  "term.depositDate": {
    label: "Date of Deposit",
    help: "The date the bank accepted the payment; interest under Sections 234B and 234C is computed from it.",
  },
  "term.advanceTax": {
    label: "Advance Tax",
    statutoryRef: "Section 208",
    help: "Tax payable in instalments during the previous year when the liability crosses the statutory threshold.",
  },
  "term.selfAssessmentTax": {
    label: "Self-Assessment Tax",
    statutoryRef: "Section 140A",
    help: "The balance tax you pay yourself before filing the return.",
  },
  "term.advanceTaxInstalment": {
    label: "Advance Tax Instalment",
    short: "Instalment",
    statutoryRef: "Section 211",
    help: "One of the statutory due dates with its cumulative percentage of the estimated liability.",
  },
  "term.surcharge": {
    label: "Surcharge",
    help: "An additional charge on tax once total income crosses the statutory thresholds.",
  },
  "term.marginalRelief": {
    label: "Marginal Relief",
    help: "Relief that caps surcharge so the extra tax never exceeds the income above the threshold.",
  },
  "term.healthEducationCess": {
    label: "Health and Education Cess",
    short: "Cess",
    help: "A cess on tax plus surcharge, levied at the statutory rate.",
  },
  "term.rebate": {
    label: "Rebate (Section 87A)",
    short: "Rebate",
    statutoryRef: "Section 87A",
    help: "A rebate for resident individuals with total income up to the statutory limit.",
  },
  "term.chapterVIA": {
    label: "Chapter VI-A Deductions",
    short: "Chapter VI-A",
    statutoryRef: "Chapter VI-A",
    help: "Deductions from gross total income; a concessional regime election forfeits most of them.",
  },
  "term.interest234A": {
    label: "Interest for Late Filing of Return (Section 234A)",
    short: "Interest 234A",
    statutoryRef: "Section 234A",
    help: "Interest for filing the return after the due date, computed on the unpaid tax.",
  },
  "term.interest234B": {
    label: "Interest for Short Payment of Advance Tax (Section 234B)",
    short: "Interest 234B",
    statutoryRef: "Section 234B",
    help: "Interest where advance tax paid falls short of ninety per cent of the assessed tax.",
  },
  "term.interest234C": {
    label: "Interest for Deferment of Advance Tax Instalments (Section 234C)",
    short: "Interest 234C",
    statutoryRef: "Section 234C",
    help: "Interest where an instalment was paid late or short of its cumulative percentage.",
  },
  "term.netPayable": {
    label: "Net Tax Payable",
    short: "Net Payable",
    help: "Total tax and interest less every credit already available.",
  },
  "term.refundDue": {
    label: "Refund Due",
    statutoryRef: "Section 237",
    help: "The excess of credits over the total tax and interest for the year.",
  },
  "term.taxOnTotalIncome": {
    label: "Tax on Total Income",
    help: "Tax charged on total income at the applicable rates, before surcharge, cess and rebate.",
  },
  "term.adjustment": {
    label: "Tax Adjustment",
    short: "Adjustment",
    help: "A difference between the books and the Income-tax Act, added back to or deducted from profit.",
  },
  "term.disallowance": {
    label: "Disallowance",
    help: "An expense in the books that the Income-tax Act does not allow as a deduction.",
  },
  "term.allowance": {
    label: "Allowance",
    help: "A deduction the Income-tax Act permits even though the books do not carry it.",
  },
  "term.returnDueDate": {
    label: "Due Date for Filing the Return",
    short: "Return Due Date",
    statutoryRef: "Section 139(1)",
    help: "Derived from the entity class and whether the accounts are subject to audit.",
  },
  "term.auditApplicable": {
    label: "Books of account subject to audit",
    short: "Audit Applicable",
    statutoryRef: "Section 44AB",
    help: "Whether the accounts must be audited; it moves the due date for filing the return.",
  },
  "term.itr6": {
    label: "Income Tax Return Form 6 (ITR-6)",
    short: "Return Form 6",
    help: "The return form for companies other than those claiming exemption under Section 11.",
  },
  "term.acknowledgementNumber": {
    label: "Acknowledgement Number",
    short: "Acknowledgement",
    help: "The number the portal issues on successful filing of the return.",
  },
  "term.verificationMode": {
    label: "Mode of Verification",
    short: "Verification",
    statutoryRef: "Section 140",
    help: "How the return is verified — electronic verification code, digital signature or physical acknowledgement.",
  },
  "term.computationRun": {
    label: "Computation Run",
    short: "Run",
    help: "One persisted execution of the computation engine, kept append-only for audit.",
  },
  "term.rulesetHash": {
    label: "Rule-set fingerprint",
    short: "Rule-set",
    help: "A fingerprint of the statutory rules used, so an old run can be reproduced exactly.",
  },
  "term.inputHash": {
    label: "Input fingerprint",
    short: "Inputs",
    help: "A fingerprint of the inputs used, so you can tell whether anything has changed since the run.",
  },
  "term.supersededRun": {
    label: "Superseded Run",
    short: "Superseded",
    help: "An earlier run replaced by a later one; it is retained, never deleted.",
  },
  "term.roundingSection288A": {
    label: "Rounding of total income (Section 288A)",
    short: "Income Rounding",
    statutoryRef: "Section 288A",
    help: "Total income is rounded to the nearest ten rupees before tax is computed.",
  },
  "term.roundingSection288B": {
    label: "Rounding of tax payable (Section 288B)",
    short: "Tax Rounding",
    statutoryRef: "Section 288B",
    help: "Tax, interest and refund figures are rounded to the nearest ten rupees.",
  },
};

/** The whole term record, or `undefined` when the code is not mapped. */
export function taxTerm(code: string): TaxTerm | undefined {
  return TAX_TERMS[code];
}

/** Full business label; falls back to `fallback`, then to the raw code. */
export function taxLabel(code: string, fallback?: string): string {
  return TAX_TERMS[code]?.label ?? fallback ?? code;
}

/** Dense-grid label — the `short` form when one exists, otherwise the full label. */
export function taxShort(code: string, fallback?: string): string {
  const term = TAX_TERMS[code];
  return term?.short ?? term?.label ?? fallback ?? code;
}

/** One-sentence explanation for an InfoTip. */
export function taxHelp(code: string): string | undefined {
  return TAX_TERMS[code]?.help;
}

/** The statutory reference chip, when the term has one. */
export function taxStatutoryRef(code: string): string | undefined {
  return TAX_TERMS[code]?.statutoryRef;
}
