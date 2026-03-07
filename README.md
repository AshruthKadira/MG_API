# Automated Expense Intelligence System (OCR + Apple Shortcuts + Raspberry Pi)

An end-to-end **AI-powered expense tracking system** that converts **UPI transaction screenshots into structured financial insights and dashboards** — with **0 operational cost**, **high OCR accuracy**, and **fully local infrastructure**.

This project was built to solve a real problem: **tracking user's construction business expenses automatically.**

Instead of manually logging transactions, this system **extracts financial data directly from payment screenshots and converts them into dashboards.**

---

# The Problem

Small businesses often struggle with expense tracking because:

- Transactions happen across **UPI apps (PhonePe, GPay, bank transfers)**
- Records exist only as **screenshots**
- Manual entry into spreadsheets is **time-consuming**
- There is **no real-time financial visibility**

The key challenge:

> **How can we convert raw transaction screenshots into structured expense intelligence automatically?**

---

# Problem Solving Approach

I approached the problem by breaking it into three key questions.

---

## 1. How can I collect all transactions in one place?

### Observation
100% of transactions were done **digitally**, and about **90% through UPI (PhonePe)**.

### Solution
Instead of building a complex banking integration system, I leveraged **human workflow + automation**.

**Process**

1. User sends transaction screenshots to a **WhatsApp group**
2. The images automatically appear in the **WhatsApp images folder on my iPhone**
3. **Apple Shortcuts** continuously scans this folder

This became the **input ingestion system**.

---

## 2.How can I understand WHY a transaction happened?

UPI receipts only contain:

- Receiver name
- Amount
- Transaction ID
- Date

But they don't explain **business context**.

### Solution: Transaction Nomenclature

I designed a simple naming format for user to send along with receipts.

```
Purpose | Role | Site Name
```

Example:

```
Cement | Contractor | Jubilee Hills Site
```

This provides **semantic context** for each transaction.

Now we know:

- **Where money is going**
- **Who received it**
- **Which project it belongs to**

---

## 3. How can I convert images into dashboards?

This was the hardest technical challenge.

### OCR Experiments

I tested multiple OCR engines.

| OCR Engine | Result |
|-------------|-------|
| Apple OCR | Rupee symbol inconsistent |
| Paddle OCR | Failed to detect currency |
| EasyOCR | Poor accuracy |
| Tesseract | Rupee symbol not detected |

### Main Problem

The **₹ (Rupee symbol)** was not being detected correctly, which broke amount extraction.

---

# Final Solution: Azure OCR Pipeline

Azure OCR successfully recognized:

- ₹ symbol
- Amount values
- Transaction metadata

And surprisingly:

> **It worked within the free tier without incurring any cost.**

---

# System Architecture

```
WhatsApp Group
      │
      ▼
iPhone WhatsApp Folder
      │
      ▼
Apple Shortcuts Automation
      │
      ▼
Image Sent to Raspberry Pi API
      │
      ▼
Flask Server (Ubuntu)
      │
      ▼
Azure OCR
      │
      ▼
JSON Post Processing
      │
      ▼
PostgreSQL Database
      │
      ▼
Power BI Dashboard
      │
      ▼
Daily Expense Insights on iPad
```

---

# Evolution of the Pipeline

This system went through **multiple iterations before reaching stability**.

---

## Pipeline V1

```
Photos → Apple OCR → Flask → PostgreSQL
```

Problems

- Apple OCR inconsistent
- Rupee symbol not detected
- Data unreliable

---

## Pipeline V2

```
Photos → Preprocess B/W → PaddleOCR → PostgreSQL
```

Problems

- Currency recognition still failed
- OCR accuracy too low

---

## Final Production Pipeline

```
Get Photos from WhatsApp Folder
        │
        ▼
Apple Shortcuts Loop
        │
        ▼
Send Image to Flask API
        │
        ▼
Azure OCR
        │
        ▼
Parse OCR JSON
        │
        ▼
Extract Amount / Receiver / Date
        │
        ▼
Save to PostgreSQL
        │
        ▼
Return Response to Shortcuts
        │
        ▼
Delete Image (avoid duplicate processing)
```

---

# Data Visualization

The processed transaction data is stored in **PostgreSQL**, which feeds into **Power BI dashboards**.

Metrics include:

- Expense by **project site**
- Expense by **purpose**
- Expense by **vendor**
- Daily / weekly / monthly spend
- Cash flow trends

The dashboard is installed on my user's **iPad**, which updates automatically.

---

# Cost

| Component | Cost |
|-----------|------|
| Raspberry Pi Server | One-time hardware |
| Azure OCR | Free tier |
| Apple Shortcuts | Free |
| PostgreSQL | Local |
| Power BI | Free |

**Total Operational Cost: 0**

---

# Security

The system is highly secure because:

- Data processing happens on **local Raspberry Pi**
- Database is **not exposed publicly**
- No third-party SaaS expense trackers
- Only **OCR API call leaves the local system**

---

# Key Features

- Fully automated expense ingestion
- OCR based financial data extraction
- Context aware transaction classification
- Real-time business dashboards
- Zero operational cost
- Privacy-preserving architecture

---

# The Most Underrated Tool: Apple Shortcuts

This project heavily relies on **Apple Shortcuts**, which acts as:

- A **mobile workflow orchestrator**
- A **trigger system**
- An **API client**
- A **loop processor**
- A **data pipeline bridge**

Despite being designed for automation on iPhones, it can function as a **lightweight ETL orchestration layer**.

This project demonstrates how **consumer automation tools can power real-world data pipelines.**

---

# Tech Stack

### Backend
- Python
- Flask
- PostgreSQL

### AI / OCR
- Azure OCR
- Paddle OCR (experimental)
- Tesseract (experimental)

### Automation
- Apple Shortcuts
- WhatsApp workflow

### Infrastructure
- Raspberry Pi
- Ubuntu Server

### Visualization
- Power BI

---

# Impact

This system now:

- Tracks **100% of business transactions**
- Eliminates **manual bookkeeping**
- Generates **daily financial insights**
- Helps make **data-driven business decisions**

And it runs **fully automatically.**

---

# Why This Project Matters

This project demonstrates:

- Real-world **AI system design**
- OCR pipeline experimentation
- Cost-optimized architecture
- Mobile automation integration
- Edge computing with Raspberry Pi
- End-to-end **data engineering pipeline**

---

#  Author

**Ashruth Kadira**

AI / ML Engineer  
Python • Data Engineering • OCR Systems • Automation • Backend Systems
