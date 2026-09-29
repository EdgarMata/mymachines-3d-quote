# MyMachines 3D Quote

Online quotation system for 3D printing services.

The application will allow customers to upload 3D models, automatically analyse their geometry, configure printing options and receive an estimated manufacturing price.

## Planned Features

* STL and 3MF file upload
* Automatic model analysis
* Bounding-box dimensions
* Model volume calculation
* Material selection
* Layer height and quality options
* Quantity calculation
* Automatic pricing
* Interactive 3D preview
* Integration with MyMachines / Odoo
* Future order and payment workflow

## Architecture

### Frontend

* React
* TypeScript
* Vite

### Backend

* Python
* FastAPI
* Trimesh

### Database

* PostgreSQL

### Infrastructure

* Docker
* Docker Compose

## Project Structure

```text
mymachines-3d-quote/
├── backend/
├── docker/
├── docs/
├── frontend/
├── .gitignore
├── docker-compose.yml
└── README.md
```

## Status

Early development.
