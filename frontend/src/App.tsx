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

  build_volume_mm: {
    x: number;
    y: number;
    z: number;
  };

  quoting_enabled: boolean;
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

type Dimensions = {
  x: number;
  y: number;
  z: number;
};

type MachineSelection = {
  status: string;
  model_dimensions_mm: Dimensions;
  default_machine: string;

  selected_machine: {
    id: string;
    label: string;
    build_volume_mm: Dimensions;
    quoting_enabled: boolean;
  } | null;

  compatible_machines: {
    id: string;
    label: string;
    build_volume_mm: Dimensions;
    quoting_enabled: boolean;
  }[];

  compatible_count: number;
  quoting_candidates_count: number;
};

type EstimateBase = {
  filename: string;
  quantity: number;
  material: string;
  quality: string;

  geometry: {
    dimensions_mm: Dimensions;
  };

  orientation: {
    status: string;
    rotation_degrees: Dimensions;
    dimensions_mm: Dimensions;

    bed_position: {
      minimum_z_mm: number;
    };

    output_file: string;
  };

  machine_selection: MachineSelection;
};

type SuccessfulEstimate = EstimateBase & {
  status: "estimated";

  slicing: {
    filament_g: number;
    material_cost_eur: number;
    layers: number;
    print_time: string;
    print_time_seconds: number;
  };

  pricing: {
    machine: string;
    quantity: number;
    selling_price_eur: number;
    minimum_order_applied: boolean;
  };
};

type ReviewEstimate = EstimateBase & {
  status: "technical_review_required" | "model_exceeds_build_volume";
  message: string;
  slicing: null;
  pricing: null;
};

type EstimateResponse = SuccessfulEstimate | ReviewEstimate;

type AnalyzeResponse = {
  filename: string;

  status: "ready" | "technical_review_required" | "model_exceeds_build_volume";

  display_machine: {
    id: string;
    label: string;
    build_volume_mm: Dimensions;
    quoting_enabled: boolean;
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

  const [displayMachine, setDisplayMachine] = useState<
    AnalyzeResponse["display_machine"] | null
  >(null);

  const [analyzing, setAnalyzing] = useState(false);

  const [analysisError, setAnalysisError] = useState<string | null>(null);

  useEffect(() => {
    async function loadOptions() {
      try {
        setLoadingOptions(true);
        setError(null);

        const response = await fetch("/api/options");

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

  useEffect(() => {
    if (!file || !options) {
      setDisplayMachine(null);
      setAnalyzing(false);
      setAnalysisError(null);
      return;
    }

    const controller = new AbortController();

    async function analyzeModel() {
      try {
        setAnalyzing(true);
        setAnalysisError(null);

        // Use the default printer while analyzing.
        setDisplayMachine(null);

        const formData = new FormData();

        formData.append("file", file!);

        const params = new URLSearchParams({
          rotation_x: orientation.x.toString(),
          rotation_y: orientation.y.toString(),
          rotation_z: orientation.z.toString(),
        });

        const response = await fetch(`/api/analyze?${params.toString()}`, {
          method: "POST",
          body: formData,
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`Model analysis failed (${response.status}).`);
        }

        const data: AnalyzeResponse = await response.json();

        if (!controller.signal.aborted) {
          setDisplayMachine(data.display_machine);
        }
      } catch (err) {
        if (controller.signal.aborted) {
          return;
        }

        setAnalysisError(
          err instanceof Error ? err.message : "Unable to analyze the model.",
        );
      } finally {
        if (!controller.signal.aborted) {
          setAnalyzing(false);
        }
      }
    }

    analyzeModel();

    return () => {
      controller.abort();
    };
  }, [file, options, orientation.x, orientation.y, orientation.z]);

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

      const response = await fetch(`/api/estimate?${params.toString()}`, {
        method: "POST",
        body: formData,
      });

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
                <p>
                  {analyzing
                    ? "Checking available print beds..."
                    : `Build plate: ${
                        displayMachine?.label ??
                        options.machines[options.defaults.machine].label
                      }`}
                </p>

                {analysisError && (
                  <p role="alert">
                    {analysisError} Showing the default build plate.
                  </p>
                )}
              </div>

              <ModelViewer
                file={file}
                orientation={orientation}
                buildVolume={
                  displayMachine?.build_volume_mm ??
                  options.machines[options.defaults.machine].build_volume_mm
                }
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
            {estimate.status === "estimated" ? (
              <>
                <div className="result-header">
                  <div>
                    <span className="eyebrow">QUOTE ESTIMATE</span>

                    <h2>Your print estimate</h2>

                    <p>{estimate.filename}</p>
                  </div>

                  <div className="price-block">
                    <span>Estimated price</span>

                    <strong>
                      €{estimate.pricing.selling_price_eur.toFixed(2)}
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

                {estimate.pricing.minimum_order_applied && (
                  <div className="notice-box">Minimum order value applied.</div>
                )}

                <p className="estimate-note">
                  This is an automatic estimate. Final production may require
                  technical review depending on geometry and printability.
                </p>
              </>
            ) : (
              <>
                <div className="result-header">
                  <div>
                    <span className="eyebrow">PROJECT REVIEW</span>

                    <h2>
                      {estimate.status === "technical_review_required"
                        ? "Technical review required"
                        : "Model exceeds available build volume"}
                    </h2>

                    <p>{estimate.filename}</p>
                  </div>
                </div>

                <div className="notice-box">{estimate.message}</div>

                <div className="dimensions">
                  <span>Print dimensions</span>

                  <strong>
                    {estimate.orientation.dimensions_mm.x} ×{" "}
                    {estimate.orientation.dimensions_mm.y} ×{" "}
                    {estimate.orientation.dimensions_mm.z} mm
                  </strong>
                </div>

                {estimate.status === "technical_review_required" && (
                  <p className="estimate-note">
                    Compatible machines identified:{" "}
                    {estimate.machine_selection.compatible_count}. An automatic
                    price is not available for this configuration. Our team will
                    need to review the project.
                  </p>
                )}

                {estimate.status === "model_exceeds_build_volume" && (
                  <p className="estimate-note">
                    Try changing the model orientation or contact our team to
                    discuss alternative production options.
                  </p>
                )}
              </>
            )}
          </section>
        )}
      </main>
    </div>
  );
}

export default App;
