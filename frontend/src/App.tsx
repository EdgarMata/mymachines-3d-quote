import { useEffect, useState } from "react";
import "./App.css";
import ModelViewer from "./components/ModelViewer";

type MaterialOption = {
  label: string;
  qualities: string[];
};

type QualityOption = {
  label: string;
  layer_height_mm: number;
};

type MachineOption = {
  label: string;
  nozzle_diameter_mm: number;
};

type OptionsResponse = {
  defaults: {
    machine: string;
    material: string;
    quality: string;
  };
  materials: Record<string, MaterialOption>;
  qualities: Record<string, QualityOption>;
  machines: Record<string, MachineOption>;
};

type EstimateResponse = {
  filename: string;
  quantity: number;
  material: string;
  quality: string;
  status: string;

  geometry: {
    dimensions_mm: {
      x: number;
      y: number;
      z: number;
    };
  };

  orientation: {
    status: string;

    rotation_degrees: {
      x: number;
      y: number;
      z: number;
    };

    dimensions_mm: {
      x: number;
      y: number;
      z: number;
    };

    bed_position: {
      minimum_z_mm: number;
    };

    output_file: string;
  };

  slicing: {
    filament_g: number;
    material_cost_eur: number;
    layers: number;
    print_time: string;
    print_time_seconds: number;
  };

  pricing: {
    production: {
      print_hours_each: number;
      total_print_hours: number;
    };

    costs: {
      material_eur: number;
      machine_depreciation_eur: number;
      maintenance_eur: number;
      electricity_eur: number;
      setup_labour_eur: number;
      postprocess_labour_eur: number;
      risk_eur: number;
    };

    summary: {
      production_cost_eur: number;
      expected_cost_eur: number;
      selling_price_before_minimum_eur: number;
      minimum_order_applied: boolean;
      selling_price_eur: number;
    };
  };
};

function App() {
  const [options, setOptions] = useState<OptionsResponse | null>(null);

  const [file, setFile] = useState<File | null>(null);

  const [orientation, setOrientation] = useState({
    x: 0,
    y: 0,
    z: 0,
  });

  const [material, setMaterial] = useState("");

  const [quality, setQuality] = useState("");

  const [quantity, setQuantity] = useState(1);

  const [loadingOptions, setLoadingOptions] = useState(true);

  const [calculating, setCalculating] = useState(false);

  const [error, setError] = useState<string | null>(null);

  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);

  useEffect(() => {
    async function loadOptions() {
      try {
        setLoadingOptions(true);
        setError(null);

        const response = await fetch("http://127.0.0.1:8000/options");

        if (!response.ok) {
          throw new Error(`Backend returned ${response.status}`);
        }

        const data: OptionsResponse = await response.json();

        setOptions(data);
        setMaterial(data.defaults.material);
        setQuality(data.defaults.quality);
      } catch (err) {
        if (err instanceof Error) {
          setError(err.message);
        } else {
          setError("Unknown error while loading options.");
        }
      } finally {
        setLoadingOptions(false);
      }
    }

    loadOptions();
  }, []);

  async function calculateQuote() {
    if (!file) {
      setError("Please select an STL file first.");
      return;
    }

    try {
      setCalculating(true);
      setError(null);
      setEstimate(null);

      const formData = new FormData();

      formData.append("file", file);

      const params = new URLSearchParams({
        quantity: quantity.toString(),
        material,
        quality,
        rotation_x: orientation.x.toString(),
        rotation_y: orientation.y.toString(),
        rotation_z: orientation.z.toString(),
      });

      const response = await fetch(
        `http://127.0.0.1:8000/estimate?${params.toString()}`,
        {
          method: "POST",
          body: formData,
        },
      );

      if (!response.ok) {
        const errorData = await response.json();

        throw new Error(
          errorData.detail ?? `Backend returned ${response.status}`,
        );
      }

      const data: EstimateResponse = await response.json();

      setEstimate(data);
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Unknown error while calculating quote.");
      }
    } finally {
      setCalculating(false);
    }
  }

  if (loadingOptions) {
    return (
      <div className="page">
        <div className="status-card">Loading printing options...</div>
      </div>
    );
  }

  if (!options) {
    return (
      <div className="page">
        <div className="status-card">
          <h2>Unable to load printing options</h2>

          {error && <p>{error}</p>}
        </div>
      </div>
    );
  }

  const availableQualities = options.materials[material]?.qualities ?? [];

  return (
    <div className="page">
      <section className="hero">
        <span className="eyebrow">MYMACHINES</span>

        <h1>Instant 3D Printing Quote</h1>

        <p>Upload your STL file and get an instant production estimate.</p>
      </section>

      <main className="quote-container">
        <section className="quote-card">
          <div className="section-heading">
            <h2>Configure your print</h2>

            <p>Choose your file, material, quality and quantity.</p>
          </div>

          <div className="form-group">
            <label htmlFor="file">3D model</label>

            <label
              className={`file-drop ${file ? "has-file" : ""}`}
              htmlFor="file"
            >
              <span className="file-icon">+</span>

              {file ? (
                <>
                  <strong>{file.name}</strong>

                  <span>Click to choose another STL file</span>
                </>
              ) : (
                <>
                  <strong>Choose an STL file</strong>

                  <span>Upload your model to calculate a quote</span>
                </>
              )}
            </label>

            <input
              className="file-input"
              id="file"
              type="file"
              accept=".stl"
              onChange={(event) => {
                const selectedFile = event.target.files?.[0] ?? null;

                setFile(selectedFile);

                setOrientation({
                  x: 0,
                  y: 0,
                  z: 0,
                });

                setEstimate(null);
                setError(null);
              }}
            />
          </div>

          {file && (
            <div className="viewer-section">
              <div className="viewer-heading">
                <h3>3D Preview</h3>

                <p>Drag to inspect the model. Scroll to zoom.</p>
              </div>

              <ModelViewer
                file={file}
                orientation={orientation}
                onOrientationChange={(newOrientation) => {
                  setOrientation(newOrientation);
                  setEstimate(null);
                  setError(null);
                }}
              />
            </div>
          )}

          <div className="form-grid">
            <div className="form-group">
              <label htmlFor="material">Material</label>

              <select
                id="material"
                value={material}
                onChange={(event) => {
                  const newMaterial = event.target.value;

                  setMaterial(newMaterial);

                  setEstimate(null);

                  const qualities = options.materials[newMaterial].qualities;

                  if (!qualities.includes(quality)) {
                    setQuality(qualities[0]);
                  }
                }}
              >
                {Object.entries(options.materials).map(
                  ([materialName, materialData]) => (
                    <option key={materialName} value={materialName}>
                      {materialData.label}
                    </option>
                  ),
                )}
              </select>
            </div>

            <div className="form-group">
              <label htmlFor="quality">Print quality</label>

              <select
                id="quality"
                value={quality}
                onChange={(event) => {
                  setQuality(event.target.value);

                  setEstimate(null);
                }}
              >
                {availableQualities.map((qualityName) => {
                  const qualityData = options.qualities[qualityName];

                  return (
                    <option key={qualityName} value={qualityName}>
                      {qualityData.label} — {qualityData.layer_height_mm} mm
                    </option>
                  );
                })}
              </select>
            </div>

            <div className="form-group">
              <label htmlFor="quantity">Quantity</label>

              <input
                id="quantity"
                type="number"
                min="1"
                value={quantity}
                onChange={(event) => {
                  setQuantity(Math.max(1, Number(event.target.value)));

                  setEstimate(null);
                }}
              />
            </div>
          </div>

          <button
            className="primary-button"
            type="button"
            onClick={calculateQuote}
            disabled={calculating || !file}
          >
            {calculating ? "Analysing model..." : "Calculate quote"}
          </button>

          {calculating && (
            <p className="processing-message">
              Slicing your model and calculating production costs. This may take
              a few seconds.
            </p>
          )}

          {error && <div className="error-box">{error}</div>}
        </section>

        {estimate && (
          <section className="result-card">
            <div className="result-header">
              <div>
                <span className="eyebrow">QUOTE ESTIMATE</span>

                <h2>Your print estimate</h2>

                <p>{estimate.filename}</p>
              </div>

              <div className="price-block">
                <span>Estimated price</span>

                <strong>
                  €{estimate.pricing.summary.selling_price_eur.toFixed(2)}
                </strong>

                <small>Excl. VAT</small>
              </div>
            </div>

            <div className="result-grid">
              <div className="result-item">
                <span>Material</span>

                <strong>{estimate.material}</strong>
              </div>

              <div className="result-item">
                <span>Quality</span>

                <strong>
                  {options.qualities[estimate.quality]?.label ??
                    estimate.quality}
                </strong>
              </div>

              <div className="result-item">
                <span>Quantity</span>

                <strong>{estimate.quantity}</strong>
              </div>

              <div className="result-item">
                <span>Print time</span>

                <strong>{estimate.slicing.print_time}</strong>
              </div>

              <div className="result-item">
                <span>Filament</span>

                <strong>{estimate.slicing.filament_g} g</strong>
              </div>

              <div className="result-item">
                <span>Layers</span>

                <strong>{estimate.slicing.layers}</strong>
              </div>
            </div>

            <div className="dimensions">
              <span>Print dimensions</span>

              <strong>
                {estimate.orientation.dimensions_mm.x} ×{" "}
                {estimate.orientation.dimensions_mm.y} ×{" "}
                {estimate.orientation.dimensions_mm.z} mm
              </strong>
            </div>

            {estimate.pricing.summary.minimum_order_applied && (
              <div className="notice-box">Minimum order value applied.</div>
            )}

            <p className="estimate-note">
              This is an automatic estimate. Final production may require
              technical review depending on geometry and printability.
            </p>
          </section>
        )}
      </main>
    </div>
  );
}

export default App;
