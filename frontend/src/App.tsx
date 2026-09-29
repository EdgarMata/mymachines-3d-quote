import { useEffect, useState } from "react";

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
  const [options, setOptions] =
    useState<OptionsResponse | null>(null);

  const [file, setFile] =
    useState<File | null>(null);

  const [material, setMaterial] =
    useState("");

  const [quality, setQuality] =
    useState("");

  const [quantity, setQuantity] =
    useState(1);

  const [loadingOptions, setLoadingOptions] =
    useState(true);

  const [calculating, setCalculating] =
    useState(false);

  const [error, setError] =
    useState<string | null>(null);

  const [estimate, setEstimate] =
    useState<EstimateResponse | null>(null);

  useEffect(() => {
    async function loadOptions() {
      try {
        setLoadingOptions(true);
        setError(null);

        const response = await fetch(
          "http://127.0.0.1:8000/options"
        );

        if (!response.ok) {
          throw new Error(
            `Backend returned ${response.status}`
          );
        }

        const data: OptionsResponse =
          await response.json();

        setOptions(data);
        setMaterial(data.defaults.material);
        setQuality(data.defaults.quality);
      } catch (err) {
        if (err instanceof Error) {
          setError(err.message);
        } else {
          setError(
            "Unknown error while loading options."
          );
        }
      } finally {
        setLoadingOptions(false);
      }
    }

    loadOptions();
  }, []);

  async function calculateQuote() {
    if (!file) {
      setError(
        "Please select an STL file first."
      );
      return;
    }

    try {
      setCalculating(true);
      setError(null);
      setEstimate(null);

      const formData = new FormData();

      formData.append(
        "file",
        file
      );

      const params = new URLSearchParams({
        quantity: quantity.toString(),
        material,
        quality,
      });

      const response = await fetch(
        `http://127.0.0.1:8000/estimate?${params.toString()}`,
        {
          method: "POST",
          body: formData,
        }
      );

      if (!response.ok) {
        const errorData =
          await response.json();

        throw new Error(
          errorData.detail ??
            `Backend returned ${response.status}`
        );
      }

      const data: EstimateResponse =
        await response.json();

      setEstimate(data);
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError(
          "Unknown error while calculating quote."
        );
      }
    } finally {
      setCalculating(false);
    }
  }

  if (loadingOptions) {
    return (
      <main>
        <h1>MyMachines 3D Quote</h1>
        <p>Loading printing options...</p>
      </main>
    );
  }

  if (!options) {
    return (
      <main>
        <h1>MyMachines 3D Quote</h1>

        <p>
          Unable to load printing options.
        </p>

        {error && <pre>{error}</pre>}
      </main>
    );
  }

  const availableQualities =
    options.materials[
      material
    ]?.qualities ?? [];

  return (
    <main>
      <h1>MyMachines 3D Quote</h1>

      <p>
        Upload your 3D model and choose
        your printing options.
      </p>

      <section>
        <div>
          <label htmlFor="file">
            STL file
          </label>

          <br />

          <input
            id="file"
            type="file"
            accept=".stl"
            onChange={(event) => {
              const selectedFile =
                event.target.files?.[0]
                ?? null;

              setFile(selectedFile);
              setEstimate(null);
            }}
          />
        </div>

        <br />

        <div>
          <label htmlFor="material">
            Material
          </label>

          <br />

          <select
            id="material"
            value={material}
            onChange={(event) => {
              const newMaterial =
                event.target.value;

              setMaterial(newMaterial);
              setEstimate(null);

              const qualities =
                options.materials[
                  newMaterial
                ].qualities;

              if (
                !qualities.includes(
                  quality
                )
              ) {
                setQuality(
                  qualities[0]
                );
              }
            }}
          >
            {Object.entries(
              options.materials
            ).map(
              ([
                materialName,
                materialData,
              ]) => (
                <option
                  key={materialName}
                  value={materialName}
                >
                  {materialData.label}
                </option>
              )
            )}
          </select>
        </div>

        <br />

        <div>
          <label htmlFor="quality">
            Print quality
          </label>

          <br />

          <select
            id="quality"
            value={quality}
            onChange={(event) => {
              setQuality(
                event.target.value
              );
              setEstimate(null);
            }}
          >
            {availableQualities.map(
              (qualityName) => {
                const qualityData =
                  options.qualities[
                    qualityName
                  ];

                return (
                  <option
                    key={qualityName}
                    value={qualityName}
                  >
                    {qualityData.label} -{" "}
                    {
                      qualityData.layer_height_mm
                    }{" "}
                    mm
                  </option>
                );
              }
            )}
          </select>
        </div>

        <br />

        <div>
          <label htmlFor="quantity">
            Quantity
          </label>

          <br />

          <input
            id="quantity"
            type="number"
            min="1"
            value={quantity}
            onChange={(event) => {
              setQuantity(
                Math.max(
                  1,
                  Number(
                    event.target.value
                  )
                )
              );

              setEstimate(null);
            }}
          />
        </div>

        <br />

        <button
          type="button"
          onClick={calculateQuote}
          disabled={
            calculating || !file
          }
        >
          {calculating
            ? "Calculating..."
            : "Calculate quote"}
        </button>
      </section>

      {error && (
        <>
          <hr />

          <section>
            <h2>Error</h2>
            <p>{error}</p>
          </section>
        </>
      )}

      {estimate && (
        <>
          <hr />

          <section>
            <h2>Quote estimate</h2>

            <p>
              File: {estimate.filename}
            </p>

            <p>
              Material:{" "}
              {estimate.material}
            </p>

            <p>
              Quality:{" "}
              {estimate.quality}
            </p>

            <p>
              Quantity:{" "}
              {estimate.quantity}
            </p>

            <h3>Printing</h3>

            <p>
              Print time each:{" "}
              {
                estimate.slicing
                  .print_time
              }
            </p>

            <p>
              Filament each:{" "}
              {
                estimate.slicing
                  .filament_g
              }{" "}
              g
            </p>

            <p>
              Layers:{" "}
              {
                estimate.slicing
                  .layers
              }
            </p>

            <h3>Price</h3>

            <p>
              Production cost: €{" "}
              {
                estimate.pricing
                  .summary
                  .production_cost_eur
              }
            </p>

            <p>
              Estimated price:
            </p>

            <strong>
              €{" "}
              {
                estimate.pricing
                  .summary
                  .selling_price_eur
              }
            </strong>

            {estimate.pricing.summary
              .minimum_order_applied && (
              <p>
                Minimum order value
                applied.
              </p>
            )}
          </section>
        </>
      )}
    </main>
  );
}

export default App;