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

![Screenshot 2025-03-25 at 1 14 24 PM](https://github.com/user-attachments/assets/8d9917f5-fb3f-482f-9cb8-0aa9d578c919)
![Screenshot 2025-03-25 at 1 17 42 PM](https://github.com/user-attachments/assets/04df9e47-7c53-45e9-b065-84d4205c13f5)
![Screenshot 2025-03-25 at 1 21 43 PM](https://github.com/user-attachments/assets/dd4488ff-aa6d-4e2f-9bc3-be04edcba9d9)
