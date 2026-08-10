import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  Group,
  IcosahedronGeometry,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  Points,
  PointsMaterial,
  Scene,
  TorusGeometry,
  WebGLRenderer,
} from 'three'

const NODE_COUNT = 110
const LINK_COUNT = 44

function fillNodePositions() {
  const positions = new Float32Array(NODE_COUNT * 3)
  const goldenAngle = Math.PI * (3 - Math.sqrt(5))

  for (let index = 0; index < NODE_COUNT; index += 1) {
    const y = 1 - (index / (NODE_COUNT - 1)) * 2
    const radius = Math.sqrt(1 - y * y)
    const theta = goldenAngle * index
    const offset = index * 3
    positions[offset] = Math.cos(theta) * radius * 1.55
    positions[offset + 1] = y * 1.55
    positions[offset + 2] = Math.sin(theta) * radius * 1.55
  }

  return positions
}

export default function createCinematicThreeField(mount) {
  const renderer = new WebGLRenderer({ alpha: true, antialias: false, powerPreference: 'low-power' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5))
  renderer.domElement.setAttribute('aria-hidden', 'true')

  const scene = new Scene()
  const camera = new PerspectiveCamera(42, 1, 0.1, 100)
  camera.position.z = 5.4
  const field = new Group()
  scene.add(field)

  const nodePositions = fillNodePositions()
  const nodeGeometry = new BufferGeometry()
  nodeGeometry.setAttribute('position', new BufferAttribute(nodePositions, 3))
  field.add(
    new Points(
      nodeGeometry,
      new PointsMaterial({
        color: 0x9a87ff,
        size: 0.048,
        transparent: true,
        opacity: 0.82,
        depthWrite: false,
        blending: AdditiveBlending,
      }),
    ),
  )

  const links = new Float32Array(LINK_COUNT * 2 * 3)
  for (let index = 0; index < LINK_COUNT; index += 1) {
    const from = index * 3
    const to = ((index * 7 + 19) % NODE_COUNT) * 3
    const linkOffset = index * 6
    links.set(nodePositions.subarray(from, from + 3), linkOffset)
    links.set(nodePositions.subarray(to, to + 3), linkOffset + 3)
  }
  const linkGeometry = new BufferGeometry()
  linkGeometry.setAttribute('position', new BufferAttribute(links, 3))
  field.add(
    new LineSegments(
      linkGeometry,
      new LineBasicMaterial({ color: 0x75e7d2, transparent: true, opacity: 0.17 }),
    ),
  )

  field.add(
    new Mesh(
      new IcosahedronGeometry(0.58, 1),
      new MeshBasicMaterial({ color: 0x9a87ff, wireframe: true, transparent: true, opacity: 0.34 }),
    ),
  )

  const ring = new Mesh(
    new TorusGeometry(2.05, 0.009, 4, 120),
    new MeshBasicMaterial({ color: 0x79d9ff, wireframe: true, transparent: true, opacity: 0.12 }),
  )
  ring.rotation.x = Math.PI / 2.7
  field.add(ring)
  mount.append(renderer.domElement)

  function resize() {
    const width = Math.max(mount.clientWidth, 1)
    const height = Math.max(mount.clientHeight, 1)
    renderer.setSize(width, height, false)
    camera.aspect = width / height
    camera.updateProjectionMatrix()
  }

  function render(pointer) {
    field.rotation.y += 0.0018
    field.rotation.x += (pointer.y * 0.08 - field.rotation.x) * 0.018
    field.rotation.z += (pointer.x * 0.06 - field.rotation.z) * 0.018
    renderer.render(scene, camera)
  }

  function dispose() {
    field.traverse((object) => {
      object.geometry?.dispose?.()
      if (Array.isArray(object.material)) object.material.forEach((material) => material.dispose())
      else object.material?.dispose?.()
    })
    renderer.dispose()
    renderer.domElement.remove()
  }

  resize()
  return { dispose, render, resize }
}
