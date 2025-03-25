# Incident Management Project

## Project Overview
This project demonstrates the configuration of a custom Incident Management System in ServiceNow, showcasing my ability to create tables, configure forms, set business rules, and manage notifications.

Key Features
- Created a `u_custom_incident` table with key fields such as:
  - `Short Description`
  - `Priority` (Choice field with High, Medium, Low)
  - `Assigned To` (Reference to sys_user table)
  - `State` (Choice field with Open, In Progress, Resolved, Closed)
- Configured Form Layout and Form Designer to ensure smooth user interaction.
- Created Business Rules to auto-assign incidents based on priority.
- Implemented Email Notifications for status updates.

How to Import Update Set
1. **Log in to ServiceNow Instance:**
   - Navigate to:System Update Sets > Retrieved Update Sets
2. **Upload XML File:**
- Click on **Import/Export > Import XML.**
- Upload `Incident_Management_Project.xml`.
3. **Preview and Commit:**
- Review changes and click **Commit Update Set.**
