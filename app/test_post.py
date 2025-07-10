from app.telegram_bot import post_to_telegram

sample_opportunity = {
    "id": 9,
    "title": "Merian Institute for Advanced Studies in Africa (MIASA) Individual Residential Fellowship 2026/2027",
    "description": (
        "Individual fellowships allow researchers to conduct a project of their own choice, "
        "connected to MIASA’s thematic research areas. Projects can be at conceptional stage, "
        "mid-term phase or final stage (analyzing data, writing up findings). Fellowships are "
        "residential with only short absences possible for activities such as conference "
        "participation and field work. Data collection cannot be the main purpose of a MIASA fellowship."
    ),
    "deadline": "September 15, 2025",
    "link": "https://miasa.ug.edu.gh/fellowship-programme/",
    "thumbnail": "https://opportunitydesk.org/wp-content/uploads/2025/07/Merian-Institute-for-Advanced-Studies-in-Africa-MIASA-Individual-Residential-Fellowship-2026-2027-768x541.jpg",
    "tags": [],
    "created_at": "2025-07-10T10:20:55.236264"
}

if __name__ == "__main__":
    success = post_to_telegram(sample_opportunity)
    print("Post sent successfully!" if success else "Failed to send post.")




# Use the following command to run the test:

# python -m app.test_post