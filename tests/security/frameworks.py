"""Risk mappings describe exercised controls, never OWASP certification."""
WEB_SOURCE = 'https://top10.owasp.org/2025/'
LLM_SOURCE = 'https://github.com/GenAI-Security-Project/GenAI-LLM-Top10/tree/main/2026/final'

RISKS = {
    'A01:2025': ('Broken Access Control', 'Shared single-user application; categories are retrieval scope, not authorization.'),
    'A02:2025': ('Security Misconfiguration', 'Headers and host/origin boundaries; deployment TLS and network exposure require review.'),
    'A03:2025': ('Software Supply Chain Failures', 'Installed-package advisories, vendored assets and static scanning are separate reports.'),
    'A04:2025': ('Cryptographic Failures', 'Actual credential persistence; transport TLS outside the loopback test is not assessed.'),
    'A05:2025': ('Injection', 'Controlled HTTP, SQL-value, filename and browser-renderer payloads; not exhaustive fuzzing.'),
    'A06:2025': ('Insecure Design', 'Explicit action boundaries and resource bounds; threat-model review remains necessary.'),
    'A07:2025': ('Authentication Failures', 'The session token is a CSRF guard, not user authentication. There are no accounts or roles.'),
    'A08:2025': ('Software or Data Integrity Failures', 'Actual uploads, retries, failed parsing and immutable existing files.'),
    'A09:2025': ('Security Logging and Alerting Failures', 'Secret exposure in real responses/logs; external alerting is not implemented or tested.'),
    'A10:2025': ('Mishandling of Exceptional Conditions', 'Malformed requests and failed document processing must leave the service usable.'),
    'LLM01:2026': ('Prompt Injection', 'Measured only with the named real model and the supplied adversarial corpus.'),
    'LLM02:2026': ('Sensitive Information Disclosure', 'Synthetic secret canaries only; no production credentials or documents are used.'),
    'LLM03:2026': ('Excessive Agency', 'Musa has no model-controlled shell or general action tools; future agents need their own assessment.'),
    'LLM04:2026': ('Supply Chain', 'Dependency advisories and asset integrity do not establish safety of model training data.'),
    'LLM05:2026': ('Data and Model Poisoning', 'Adversarial retrieved documents; training-time or weight poisoning is not covered.'),
    'LLM06:2026': ('Unbounded Consumption', 'Request/output bounds and actual token counts; not a load or denial-of-service stress test.'),
    'LLM07:2026': ('Misinformation', 'Closed factual and unanswerable cases with known references, not general answer accuracy.'),
    'LLM08:2026': ('Hidden Context Exposure', 'Credential and out-of-scope canaries; public system instructions are not treated as secrets.'),
    'LLM09:2026': ('Vector and Embedding Weaknesses', 'Actual CPU embeddings and category scoping; this is not multi-user isolation.'),
    'LLM10:2026': ('Improper Output Handling', 'Real browser execution of the production Markdown renderer and document viewer.'),
}


def coverage(results):
    return [{
        'id': identifier, 'name': title,
        'source': WEB_SOURCE if identifier.startswith('A') else LLM_SOURCE,
        'checks': [row['id'] for row in results if identifier in row['risks']],
        'executed': sum(row['status'] in {'pass', 'fail', 'error'} for row in results if identifier in row['risks']),
        'failed': sum(row['status'] in {'fail', 'error'} for row in results if identifier in row['risks']),
        'limits': limits,
    } for identifier, (title, limits) in RISKS.items()]
