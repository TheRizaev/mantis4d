# Room Designer - AI Interior Design MVP

A web application that uses AI to generate interior designs based on room parameters and reference images.

## Features

- **User Management**: Simple username-based authentication
- **Room Management**: Create, edit, and delete rooms
- **Token System**: Start with 1000 tokens, each generation costs 300 tokens
- **Image Upload**: Upload room photos and reference style images
- **AI Generation**: Uses OpenAI GPT-4o-mini for style analysis and DALL-E-3 for image generation
- **Responsive UI**: Modern, mobile-friendly interface

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Set Up OpenAI API Key

Replace the API key in `app.py` with your own OpenAI API key:

```python
client = OpenAI(api_key="your-api-key-here")
```

### 3. Run the Application

```bash
python app.py
```

The application will be available at `http://localhost:5000`

### 4. Database

The application automatically creates a SQLite database (`app.db`) with the following tables:
- `users`: User accounts with token balances
- `rooms`: Room projects with parameters and image paths

## Usage

1. **Login**: Enter a username to create or access your account
2. **Create Room**: Click "Create New Room" to start a new project
3. **Fill Details**: Enter room dimensions, style preferences, and upload images
4. **Generate**: Click "Generate Interior Design" to create AI-generated designs
5. **View Results**: Generated images appear in the result area

## File Structure

```
├── app.py                 # Flask backend application
├── requirements.txt       # Python dependencies
├── templates/
│   ├── login.html        # Login page
│   └── index.html        # Main dashboard
├── static/               # Static files (CSS, JS, images)
├── uploads/              # Uploaded images (created automatically)
└── app.db               # SQLite database (created automatically)
```

## API Endpoints

- `GET /` - Main dashboard
- `GET/POST /login` - User authentication
- `GET /api/rooms` - List user's rooms
- `POST /api/rooms` - Create new room
- `PUT /api/rooms/<id>` - Update room
- `DELETE /api/rooms/<id>` - Delete room
- `POST /api/upload` - Upload images
- `POST /api/generate` - Generate interior design
- `GET /api/user/tokens` - Get user token balance

## Token System

- New users start with 1000 tokens
- Each interior generation costs 300 tokens
- Users can create unlimited rooms but need tokens to generate designs

## Technologies Used

- **Backend**: Flask (Python)
- **Database**: SQLite
- **AI**: OpenAI GPT-4o-mini + DALL-E-3
- **Frontend**: HTML5, CSS3, JavaScript
- **File Handling**: Werkzeug secure file uploads

## Development Notes

- The application runs in debug mode by default
- Uploaded files are stored in the `uploads/` directory
- Images are served via Flask's static file serving
- The database is automatically initialized on first run

## Production Considerations

- Change the secret key in `app.py`
- Use a proper database (PostgreSQL, MySQL) for production
- Implement proper user authentication (passwords, sessions)
- Add input validation and error handling
- Set up proper file storage (AWS S3, etc.)
- Add rate limiting and security measures

