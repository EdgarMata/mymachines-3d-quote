import { Suspense, useEffect, useMemo, useState } from "react";

import { Canvas, type ThreeEvent } from "@react-three/fiber";

import {
  Bounds,
  Edges,
  Environment,
  Grid,
  OrbitControls,
} from "@react-three/drei";

import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

import * as THREE from "three";

type Orientation = {
  x: number;
  y: number;
  z: number;
};

type BuildVolume = {
  x: number;
  y: number;
  z: number;
};

type ModelViewerProps = {
  file: File;

  orientation: Orientation;

  buildVolume: BuildVolume;

  onOrientationChange: (orientation: Orientation) => void;
};

function STLModel({
  file,
  orientation,
  placeFaceMode,
  onFaceSelected,
}: {
  file: File;
  orientation: Orientation;
  placeFaceMode: boolean;
  onFaceSelected: (normal: THREE.Vector3) => void;
}) {
  const [baseGeometry, setBaseGeometry] = useState<THREE.BufferGeometry | null>(
    null,
  );

  useEffect(() => {
    let active = true;

    async function loadModel() {
      const buffer = await file.arrayBuffer();

      if (!active) {
        return;
      }

      const loader = new STLLoader();

      const geometry = loader.parse(buffer);

      geometry.computeVertexNormals();
      geometry.computeBoundingBox();
      geometry.computeBoundingSphere();

      setBaseGeometry((previousGeometry) => {
        previousGeometry?.dispose();

        return geometry;
      });
    }

    loadModel();

    return () => {
      active = false;
    };
  }, [file]);

  const displayedGeometry = useMemo(() => {
    if (!baseGeometry) {
      return null;
    }

    const geometry = baseGeometry.clone();

    const rotation = new THREE.Euler(
      THREE.MathUtils.degToRad(orientation.x),
      THREE.MathUtils.degToRad(orientation.y),
      THREE.MathUtils.degToRad(orientation.z),
      "XYZ",
    );

    const rotationMatrix = new THREE.Matrix4();

    rotationMatrix.makeRotationFromEuler(rotation);

    geometry.applyMatrix4(rotationMatrix);

    geometry.computeBoundingBox();

    if (geometry.boundingBox) {
      const box = geometry.boundingBox;

      const centerX = (box.min.x + box.max.x) / 2;

      const centerY = (box.min.y + box.max.y) / 2;

      const bottomZ = box.min.z;

      geometry.translate(-centerX, -centerY, -bottomZ);
    }

    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();

    return geometry;
  }, [baseGeometry, orientation.x, orientation.y, orientation.z]);

  useEffect(() => {
    return () => {
      displayedGeometry?.dispose();
    };
  }, [displayedGeometry]);

  if (!displayedGeometry) {
    return null;
  }

  function handlePointerDown(event: ThreeEvent<PointerEvent>) {
    if (!placeFaceMode || !event.face) {
      return;
    }

    event.stopPropagation();

    const normal = event.face.normal.clone().normalize();

    onFaceSelected(normal);
  }

  return (
    <mesh
      geometry={displayedGeometry}
      castShadow
      receiveShadow
      onPointerDown={handlePointerDown}
    >
      <meshStandardMaterial color="#39aaf5" roughness={0.48} metalness={0.08} />
    </mesh>
  );
}

function ModelViewer({
  file,
  orientation,
  buildVolume,
  onOrientationChange,
}: ModelViewerProps) {
  const [placeFaceMode, setPlaceFaceMode] = useState(false);

  function rotateAxis(axis: keyof Orientation) {
    onOrientationChange({
      ...orientation,

      [axis]: (orientation[axis] + 90) % 360,
    });
  }

  function resetOrientation() {
    setPlaceFaceMode(false);

    onOrientationChange({
      x: 0,
      y: 0,
      z: 0,
    });
  }

  function placeFaceOnBed(normal: THREE.Vector3) {
    const currentEuler = new THREE.Euler(
      THREE.MathUtils.degToRad(orientation.x),
      THREE.MathUtils.degToRad(orientation.y),
      THREE.MathUtils.degToRad(orientation.z),
      "XYZ",
    );

    const currentQuaternion = new THREE.Quaternion().setFromEuler(currentEuler);

    const bedNormal = new THREE.Vector3(0, 0, -1);

    const correction = new THREE.Quaternion().setFromUnitVectors(
      normal,
      bedNormal,
    );

    const newQuaternion = correction.multiply(currentQuaternion);

    const newEuler = new THREE.Euler().setFromQuaternion(newQuaternion, "XYZ");

    function cleanAngle(radians: number) {
      let degrees = THREE.MathUtils.radToDeg(radians);

      if (Math.abs(degrees) < 0.001) {
        degrees = 0;
      }

      return Math.round(degrees * 100) / 100;
    }

    onOrientationChange({
      x: cleanAngle(newEuler.x),
      y: cleanAngle(newEuler.y),
      z: cleanAngle(newEuler.z),
    });

    setPlaceFaceMode(false);
  }

  return (
    <div className="model-viewer-wrapper">
      <div className="model-viewer">
        <Canvas
          camera={{
            position: [180, -180, 140],
            fov: 45,
          }}
          shadows
          onCreated={({ camera }) => {
            camera.up.set(0, 0, 1);
          }}
        >
          <color attach="background" args={["#f7f8fa"]} />

          <ambientLight intensity={1.2} />

          <directionalLight
            position={[120, -120, 180]}
            intensity={2}
            castShadow
          />

          <Suspense fallback={null}>
            {/* Print bed - Prusa MK3S */}
            <mesh position={[0, 0, -0.15]} receiveShadow>
              <planeGeometry args={[buildVolume.x, buildVolume.y]} />

              <meshStandardMaterial
                color="#edf1f5"
                roughness={0.85}
                metalness={0.05}
                side={THREE.FrontSide}
              />

              <Edges color="#39aaf5" threshold={15} />
            </mesh>

            {/* Print bed grid */}
            <Grid
              args={[buildVolume.x, buildVolume.y]}
              rotation={[Math.PI / 2, 0, 0]}
              position={[0, 0, -0.13]}
              infiniteGrid={false}
              cellSize={10}
              cellThickness={0.6}
              cellColor="#b5c9d8"
              sectionSize={50}
              sectionThickness={1.2}
              sectionColor="#69a9d3"
              fadeDistance={1000}
              fadeStrength={0}
            />

            {/* Automatic camera framing: STL only */}
            <Bounds fit clip observe margin={1.3}>
              <STLModel
                file={file}
                orientation={orientation}
                placeFaceMode={placeFaceMode}
                onFaceSelected={placeFaceOnBed}
              />
            </Bounds>

            <Environment preset="studio" />
          </Suspense>

          <OrbitControls
            makeDefault
            enableDamping
            dampingFactor={0.08}
            enableRotate={!placeFaceMode}
          />
        </Canvas>
      </div>

      <div className="orientation-panel">
        <div className="orientation-info">
          <div>
            <strong>Print orientation</strong>

            <p>
              Mouse controls only change the camera view. Use these buttons to
              rotate the model for printing.
            </p>
          </div>

          <div className="orientation-values">
            <span>X {orientation.x}°</span>

            <span>Y {orientation.y}°</span>

            <span>Z {orientation.z}°</span>
          </div>
        </div>

        {placeFaceMode && (
          <div className="face-selection-message">
            Click the model surface you want to place on the print bed.
          </div>
        )}

        <div className="orientation-actions">
          <button
            type="button"
            className={
              placeFaceMode ? "place-face-button active" : "place-face-button"
            }
            onClick={() => setPlaceFaceMode(!placeFaceMode)}
          >
            {placeFaceMode ? "Cancel face selection" : "Place face on bed"}
          </button>

          <button type="button" onClick={() => rotateAxis("x")}>
            Rotate X 90°
          </button>

          <button type="button" onClick={() => rotateAxis("y")}>
            Rotate Y 90°
          </button>

          <button type="button" onClick={() => rotateAxis("z")}>
            Rotate Z 90°
          </button>

          <button
            type="button"
            className="orientation-reset"
            onClick={resetOrientation}
          >
            Reset
          </button>
        </div>
      </div>
    </div>
  );
}

export default ModelViewer;
