"""
A small "grounded enterprise context" corpus (spec 3.2.3: "grounded
enterprise context vectors").

In a real deployment this would be the customer's actual knowledge base —
Confluence pages, product docs, policy PDFs — ingested into a vector store.
For this build we ship a compact, self-contained corpus for a fictional
company, "Aperture Cloud Inc.", so the Performance Agent's grounding check
is demonstrable end-to-end without any external dependency. Swap this
module's `DOCUMENTS` list (or point kb/retriever.py at a real vector DB)
to point ControlPlane.ai at a real enterprise's data.
"""
from __future__ import annotations

DOCUMENTS: list[dict] = [
    {
        "id": "refund-policy",
        "title": "Refund & Cancellation Policy",
        "text": (
            "Aperture Cloud offers a 14-day money-back guarantee on all new "
            "subscriptions. Refund requests must be submitted within 14 days "
            "of the initial charge via the billing portal. Refunds are issued "
            "to the original payment method within 5-7 business days. "
            "Annual plans cancelled after the 14-day window are not eligible "
            "for a prorated refund, but will not renew at the next billing "
            "cycle."
        ),
    },
    {
        "id": "sla-uptime",
        "title": "Service Level Agreement",
        "text": (
            "Aperture Cloud guarantees 99.9% monthly uptime for all Business "
            "and Enterprise tier customers. If uptime falls below 99.9% in a "
            "calendar month, affected customers receive a service credit "
            "equal to 10% of that month's fees, up to a maximum of 30% for "
            "outages exceeding 8 hours cumulative downtime. Credits are "
            "applied automatically to the next invoice."
        ),
    },
    {
        "id": "pricing-tiers",
        "title": "Pricing & Plans",
        "text": (
            "Aperture Cloud has three pricing tiers: Starter at $29/month "
            "(up to 5 seats, 100GB storage), Business at $99/month (up to 25 "
            "seats, 1TB storage, priority support), and Enterprise which is "
            "custom-priced and includes unlimited seats, dedicated support, "
            "and a signed SLA. All plans are billed monthly or annually with "
            "a 15% discount for annual commitments."
        ),
    },
    {
        "id": "password-reset",
        "title": "Account Security & Password Reset",
        "text": (
            "Users can reset their password from the login page by clicking "
            "'Forgot Password'. A reset link is emailed and expires after 30 "
            "minutes. Enterprise customers with SSO enabled must reset "
            "credentials through their identity provider; Aperture Cloud "
            "support cannot reset SSO-managed passwords directly."
        ),
    },
    {
        "id": "data-retention",
        "title": "Data Retention & Deletion Policy",
        "text": (
            "Customer data is retained for 90 days after account "
            "cancellation, after which it is permanently purged from "
            "production systems and encrypted backups within an additional "
            "30 days. Customers may request immediate deletion by contacting "
            "privacy@aperturecloud.example, subject to a 5 business day "
            "processing window."
        ),
    },
    {
        "id": "security-certifications",
        "title": "Security & Compliance Certifications",
        "text": (
            "Aperture Cloud is SOC 2 Type II certified and ISO 27001 "
            "compliant. Customer data is encrypted at rest using AES-256 and "
            "in transit using TLS 1.2 or higher. Aperture Cloud does not "
            "currently hold HIPAA or FedRAMP certification."
        ),
    },
    {
        "id": "support-hours",
        "title": "Customer Support Hours",
        "text": (
            "Standard support (Starter and Business tiers) is available "
            "Monday-Friday, 9am-6pm Eastern Time, via email and chat with a "
            "target first response time of 4 business hours. Enterprise "
            "customers receive 24/7 phone and chat support with a 1-hour "
            "response SLA for Severity 1 incidents."
        ),
    },
    {
        "id": "api-rate-limits",
        "title": "API Rate Limits",
        "text": (
            "The Aperture Cloud REST API enforces a default rate limit of "
            "600 requests per minute per API key on the Business tier and "
            "2,000 requests per minute on Enterprise. Requests beyond the "
            "limit receive an HTTP 429 response with a Retry-After header. "
            "Rate limits can be raised for Enterprise customers on request."
        ),
    },
    {
        "id": "onboarding",
        "title": "Onboarding Checklist",
        "text": (
            "New Business and Enterprise customers are assigned an "
            "onboarding specialist who schedules a kickoff call within 3 "
            "business days of signup. Onboarding typically covers workspace "
            "setup, SSO configuration, data import, and a training session "
            "for admins. Self-serve Starter customers onboard via an "
            "in-product guided tour."
        ),
    },
    {
        "id": "product-overview",
        "title": "Product Overview",
        "text": (
            "Aperture Cloud is a workflow automation platform that connects "
            "to over 200 third-party apps. Core features include visual "
            "workflow building, scheduled triggers, webhook support, and "
            "an audit log of every automation run. It does not currently "
            "offer a native mobile app; the web app is mobile-responsive."
        ),
    },
]
