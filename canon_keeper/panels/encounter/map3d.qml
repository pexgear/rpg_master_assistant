// The battle map, stood up. Draws what `scene` (view3d.Scene3D) says and
// reports clicks back to it; it decides nothing about the fight.
import QtQuick
import QtQuick3D

Item {
    id: root

    readonly property real sq: 100
    // The camera, as a turn about the middle of the room. Degrees; 90 pitch is
    // straight down.
    property real yaw: 0
    property real pitch: 55
    property real dist: 2000
    property real tx: 0
    property real tz: 0
    // True once somebody has turned, zoomed or dragged the room. Until then
    // the camera keeps the whole room in view as the panel changes size, the
    // way the flat map fits itself until somebody zooms it.
    property bool touched: false
    // Nothing reads this for its value. Every label that is pinned to a point
    // in the room names it, so that moving the camera moves the labels too:
    // a projection is not something QML knows to recompute on its own.
    readonly property var viewKey: [yaw, pitch, dist, tx, tz, width, height]

    // How far back the camera has to stand to get the whole room in. Worked
    // out for the room's near edge, which is the part a tilted camera loses
    // first: it is both the closest to the lens and the lowest on screen.
    function fitDistance(towardPitch) {
        var t = Math.tan(20 * Math.PI / 180)
        var aspect = Math.max(0.2, width / Math.max(1, height))
        var p = towardPitch * Math.PI / 180
        var deep = scene.rows * sq / 2 + sq * 1.1
        var wide = scene.columns * sq / 2 + sq * 0.6
        return deep * Math.cos(p) + Math.max(deep * Math.sin(p) / t, wide / (t * aspect))
    }

    function look(toYaw, toPitch) {
        glide.stop()
        touched = false
        yawTo.to = toYaw
        pitchTo.to = toPitch
        distTo.to = fitDistance(toPitch)
        glide.start()
    }
    function tilted() { look(0, 55) }
    function above() { look(0, 90) }

    function zoomBy(notches) {
        glide.stop()
        touched = true
        dist = Math.max(300, Math.min(20000, dist * Math.pow(1.2, -notches)))
    }

    function panBy(dx, dy) {
        glide.stop()
        touched = true
        var a = yaw * Math.PI / 180
        tx += (Math.cos(a) * dx + Math.sin(a) * dy) * sq
        tz += (-Math.sin(a) * dx + Math.cos(a) * dy) * sq
    }

    // A spot on the floor, or on the thing standing on it, under a pixel.
    function groundAt(x, y) {
        var hit = view.pick(x, y)
        if (!hit.objectHit)
            return null
        var thing = hit.objectHit
        if (thing.tid !== undefined)
            return { x: hit.scenePosition.x, z: hit.scenePosition.z, tid: thing.tid }
        // A wall is its own square, not whichever neighbour its side leans into.
        if (thing.anchorX !== undefined)
            return { x: thing.anchorX, z: thing.anchorZ, tid: -1 }
        return { x: hit.scenePosition.x, z: hit.scenePosition.z, tid: -1 }
    }

    function project(x, y, z, _key) {
        return view.mapFrom3DScene(Qt.vector3d(x, y, z))
    }

    function refit() {
        if (!touched && !glide.running)
            dist = fitDistance(pitch)
    }
    onWidthChanged: refit()
    onHeightChanged: refit()
    Component.onCompleted: refit()
    Connections {
        target: scene
        // A different room is a different thing to look at.
        function onShapeChanged() { root.tx = 0; root.tz = 0; root.touched = false; root.refit() }
    }

    ParallelAnimation {
        id: glide
        NumberAnimation { id: yawTo; target: root; property: "yaw"; duration: 350; easing.type: Easing.InOutQuad }
        NumberAnimation { id: pitchTo; target: root; property: "pitch"; duration: 350; easing.type: Easing.InOutQuad }
        NumberAnimation { id: distTo; target: root; property: "dist"; duration: 350; easing.type: Easing.InOutQuad }
        NumberAnimation { target: root; property: "tx"; to: 0; duration: 350; easing.type: Easing.InOutQuad }
        NumberAnimation { target: root; property: "tz"; to: 0; duration: 350; easing.type: Easing.InOutQuad }
    }

    View3D {
        id: view
        anchors.fill: parent
        // Named rather than found: projecting a label onto the screen asks the
        // view for its camera, and an implicit one answers with nothing.
        camera: camera

        environment: SceneEnvironment {
            backgroundMode: SceneEnvironment.Color
            clearColor: scene.backdrop
            antialiasingMode: SceneEnvironment.MSAA
            antialiasingQuality: SceneEnvironment.High
        }

        Node {
            position: Qt.vector3d(root.tx, 0, root.tz)
            eulerRotation.y: root.yaw
            Node {
                eulerRotation.x: -root.pitch
                PerspectiveCamera {
                    id: camera
                    z: root.dist
                    fieldOfView: 40
                    clipNear: 10
                    clipFar: 60000
                }
            }
        }

        DirectionalLight {
            eulerRotation.x: -60
            eulerRotation.y: -35
            brightness: 1.0
            castsShadow: true
            shadowFactor: 35
            shadowMapQuality: Light.ShadowMapQualityHigh
        }
        // A weaker light from the other side, so the unlit face of a wall is
        // grey rather than black -- a black face reads as a hole.
        DirectionalLight {
            eulerRotation.x: -35
            eulerRotation.y: 145
            brightness: 0.45
        }

        Model {
            id: floor
            source: "#Rectangle"
            eulerRotation.x: -90
            scale: Qt.vector3d(scene.columns, scene.rows, 1)
            pickable: true
            receivesShadows: true
            materials: PrincipledMaterial {
                roughness: 1
                // The floor's squares, drawn once into a texture: the same lines
                // as the flat map, heavier every fifth and heaviest through 0,0.
                baseColorMap: Texture {
                    sourceItem: Canvas {
                        id: lines
                        readonly property int cell: Math.max(8, Math.min(48, Math.floor(4096 / Math.max(scene.columns, scene.rows))))
                        width: scene.columns * cell
                        height: scene.rows * cell
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.reset()
                            ctx.fillStyle = scene.floor
                            ctx.fillRect(0, 0, width, height)
                            function ink(here) {
                                return here === 0 ? scene.axis : (here % 5 === 0 ? scene.strong : scene.faint)
                            }
                            for (var i = 0; i <= scene.columns; i++) {
                                ctx.strokeStyle = ink(scene.left + i)
                                ctx.lineWidth = scene.left + i === 0 ? 3 : 2
                                ctx.beginPath(); ctx.moveTo(i * cell, 0); ctx.lineTo(i * cell, height); ctx.stroke()
                            }
                            for (var j = 0; j <= scene.rows; j++) {
                                ctx.strokeStyle = ink(scene.top + j)
                                ctx.lineWidth = scene.top + j === 0 ? 3 : 2
                                ctx.beginPath(); ctx.moveTo(0, j * cell); ctx.lineTo(width, j * cell); ctx.stroke()
                            }
                        }
                        Connections {
                            target: scene
                            function onShapeChanged() { lines.requestPaint() }
                            function onColoursChanged() { lines.requestPaint() }
                        }
                    }
                }
            }
        }

        Repeater3D {
            model: scene.walls
            Model {
                readonly property real anchorX: modelData.sx
                readonly property real anchorZ: modelData.sz
                source: "#Cube"
                position: Qt.vector3d(modelData.sx, scene.wallHeight * root.sq / 2, modelData.sz)
                scale: Qt.vector3d(1, scene.wallHeight, 1)
                pickable: true
                castsShadows: true
                receivesShadows: true
                materials: PrincipledMaterial { baseColor: scene.stone; roughness: 0.9 }
            }
        }

        Repeater3D {
            model: scene.tokens
            Node {
                id: figure
                required property int tid
                required property real sx
                required property real sz
                required property color fill
                required property real alpha
                required property real size
                required property bool turn
                required property bool down

                position: Qt.vector3d(sx, 0, sz)
                scale: Qt.vector3d(size, size, size)

                PrincipledMaterial {
                    id: skin
                    baseColor: figure.fill
                    roughness: 0.6
                    opacity: figure.alpha
                    alphaMode: figure.alpha < 1 ? PrincipledMaterial.Blend : PrincipledMaterial.Opaque
                }

                // Whose turn it is: a gold disc under their feet, wider than
                // the base so it shows as a ring round it.
                Model {
                    visible: figure.turn
                    source: "#Cylinder"
                    y: 1
                    scale: Qt.vector3d(0.96, 0.02, 0.96)
                    materials: PrincipledMaterial { baseColor: scene.turn; lighting: PrincipledMaterial.NoLighting }
                }
                Model {
                    readonly property int tid: figure.tid
                    source: "#Cylinder"
                    y: 5
                    scale: Qt.vector3d(0.8, 0.1, 0.8)
                    pickable: true
                    materials: [skin]
                }
                // A body on the floor is its base and nothing standing: they
                // are lying where they fell.
                Model {
                    readonly property int tid: figure.tid
                    visible: !figure.down
                    source: "#Cylinder"
                    y: 47
                    scale: Qt.vector3d(0.44, 0.75, 0.44)
                    pickable: !figure.down
                    materials: [skin]
                }
                Model {
                    readonly property int tid: figure.tid
                    visible: !figure.down
                    source: "#Sphere"
                    y: 100
                    scale: Qt.vector3d(0.38, 0.38, 0.38)
                    pickable: !figure.down
                    materials: [skin]
                }
            }
        }

        Repeater3D {
            model: scene.marks
            Model {
                readonly property string kind: modelData.kind
                readonly property color tint: modelData.near ? scene.plan : scene.hurt
                position: Qt.vector3d(
                    modelData.sx,
                    kind === "target" ? 175 : kind === "ghost" ? 40 : 1.5,
                    modelData.sz)
                source: kind === "target" ? "#Cone"
                      : kind === "selected" ? "#Rectangle"
                      : "#Cylinder"
                eulerRotation.x: kind === "selected" ? -90 : kind === "target" ? 180 : 0
                scale: kind === "selected" ? Qt.vector3d(0.98, 0.98, 1)
                     : kind === "step" ? Qt.vector3d(0.2, 0.01, 0.2)
                     : kind === "ghost" ? Qt.vector3d(0.7, 0.8, 0.7)
                     : Qt.vector3d(0.5, 0.6, 0.5)
                castsShadows: false
                materials: PrincipledMaterial {
                    lighting: kind === "ghost" ? PrincipledMaterial.FragmentLighting : PrincipledMaterial.NoLighting
                    baseColor: kind === "selected" ? scene.highlight
                             : kind === "target" ? scene.blade
                             : tint
                    opacity: kind === "selected" ? 0.35 : kind === "ghost" ? 0.4 : 1
                    alphaMode: opacity < 1 ? PrincipledMaterial.Blend : PrincipledMaterial.Opaque
                }
            }
        }
    }

    // Initials and what is left of a turn, over each figure's head. Drawn flat
    // on the screen rather than in the room, so they face you from any angle.
    Repeater {
        model: scene.tokens
        Item {
            required property real sx
            required property real sz
            required property real size
            required property real alpha
            required property string initials
            required property var pips
            readonly property vector3d spot: root.project(sx, (pips.length ? 150 : 140) * size + 20, sz, root.viewKey)
            x: spot.x
            y: spot.y
            visible: spot.z > 0
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.top
                text: parent.initials
                color: "white"
                style: Text.Outline
                styleColor: "#202020"
                font.pixelSize: 13
                font.bold: true
                opacity: parent.alpha
            }
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                anchors.topMargin: 2
                spacing: 3
                Repeater {
                    model: pips
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        color: modelData.filled ? modelData.colour : "transparent"
                        border.width: modelData.filled ? 1 : 2
                        border.color: modelData.filled ? scene.floor : modelData.colour
                    }
                }
            }
        }
    }

    // The coordinates, along the near and left edges of the floor. What makes
    // a square something people can say out loud -- "the one at minus three,
    // two" -- so they are drawn at every angle, thinned out as the room gets
    // small the way the flat map's rulers were: every square, every second,
    // or every fifth, and 0 always.
    readonly property real squarePx: {
        var a = project(0, 0, 0, viewKey)
        var b = project(sq, 0, 0, viewKey)
        return Math.hypot(b.x - a.x, b.y - a.y)
    }
    readonly property int every: squarePx >= 24 ? 1 : squarePx >= 12 ? 2 : 5

    Repeater {
        model: scene.columns
        Text {
            required property int index
            readonly property int here: scene.left + index
            readonly property vector3d spot: root.project(
                (index + 0.5 - scene.columns / 2) * root.sq, 0,
                scene.rows * root.sq / 2 + 45, root.viewKey)
            visible: spot.z > 0 && (here === 0 || here % root.every === 0)
            x: spot.x - width / 2
            y: spot.y - height / 2
            text: here
            color: scene.ink
            font.pixelSize: 11
            font.bold: here === 0
        }
    }
    Repeater {
        model: scene.rows
        Text {
            required property int index
            readonly property int here: scene.top + index
            readonly property vector3d spot: root.project(
                -scene.columns * root.sq / 2 - 45, 0,
                (index + 0.5 - scene.rows / 2) * root.sq, root.viewKey)
            visible: spot.z > 0 && (here === 0 || here % root.every === 0)
            x: spot.x - width / 2
            y: spot.y - height / 2
            text: here
            color: scene.ink
            font.pixelSize: 11
            font.bold: here === 0
        }
    }

    // Damage rising off whoever took it.
    Repeater {
        model: scene.floats
        Text {
            readonly property vector3d spot: root.project(modelData.sx, 170 + modelData.rise * 100, modelData.sz, root.viewKey)
            x: spot.x - width / 2
            y: spot.y - height / 2
            text: modelData.text
            color: modelData.colour
            opacity: modelData.alpha
            style: Text.Outline
            styleColor: "#202020"
            font.pixelSize: 20
            font.bold: true
        }
    }

    MouseArea {
        id: pointer
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
        hoverEnabled: true
        property point last
        property real moved: 0
        property int held: 0
        // Decided at the press as well as while moving, so letting go of
        // Shift part-way through a slide does not turn it into a spin.
        property bool slideHeld: false

        function report(mouse, button) {
            var spot = root.groundAt(mouse.x, mouse.y)
            if (spot === null) {
                if (button === Qt.LeftButton)
                    scene.clickNothing()
                return
            }
            scene.click(spot.x, spot.z, spot.tid, button, mouse.modifiers, mouse.x, mouse.y)
        }

        onPressed: (mouse) => {
            root.forceActiveFocus()
            last = Qt.point(mouse.x, mouse.y)
            moved = 0
            held = mouse.button
            slideHeld = mouse.button === Qt.MiddleButton
                || (mouse.button === Qt.LeftButton && (mouse.modifiers & Qt.ShiftModifier))
            // A right-click is a menu, and a menu should not wait for a drag
            // that is never coming.
            if (mouse.button === Qt.RightButton)
                report(mouse, Qt.RightButton)
        }
        onPositionChanged: (mouse) => {
            if (!pressed) {
                var spot = root.groundAt(mouse.x, mouse.y)
                if (spot === null) scene.hoverNothing()
                else scene.hover(spot.x, spot.z, spot.tid)
                return
            }
            var dx = mouse.x - last.x
            var dy = mouse.y - last.y
            last = Qt.point(mouse.x, mouse.y)
            moved += Math.abs(dx) + Math.abs(dy)
            if (moved < 5)
                return
            glide.stop()
            root.touched = true
            // Read off the buttons held now rather than the one that started
            // the drag: some mouse drivers report a wheel-click oddly at the
            // press and correctly after. Shift with the left button pans too,
            // for a touchpad, which has no middle button to hold.
            var sliding = slideHeld || (mouse.buttons & Qt.MiddleButton)
                || ((mouse.buttons & Qt.LeftButton) && (mouse.modifiers & Qt.ShiftModifier))
            if (sliding) {
                // The floor follows the pointer, as near as a tilted floor can.
                var k = root.dist / Math.max(1, root.height) * 0.8
                root.panBy(-dx * k / root.sq, -dy * k / root.sq)
            } else if (mouse.buttons & Qt.LeftButton) {
                root.yaw -= dx * 0.3
                root.pitch = Math.max(15, Math.min(90, root.pitch + dy * 0.25))
            }
        }
        // A click is a press and a release in about the same place. Anything
        // further is somebody turning the room, and must not also select
        // whatever they happened to let go over.
        onReleased: (mouse) => {
            if (held === Qt.LeftButton && moved < 5)
                report(mouse, Qt.LeftButton)
        }
        onExited: scene.hoverNothing()
        onWheel: (wheel) => root.zoomBy(wheel.angleDelta.y > 0 ? 1 : -1)
    }

    DropArea {
        anchors.fill: parent
        enabled: !scene.readOnly
        keys: ["application/x-canonkeeper-combatant"]
        onDropped: (drop) => {
            var spot = root.groundAt(drop.x, drop.y)
            if (spot === null)
                return
            scene.drop(spot.x, spot.z, drop.getDataAsString("application/x-canonkeeper-combatant"))
            drop.accept()
        }
    }

    // The ways back to a known view, where the eye is while the hand is lost.
    Row {
        x: 8
        y: 8
        spacing: 6
        Repeater {
            model: [
                { label: "Tilted", tip: "The room at an angle, north at the top (0)", run: function () { root.tilted() } },
                { label: "From above", tip: "Straight down, like the flat map", run: function () { root.above() } },
                { label: "Low walls", tip: "Knock the walls down to see behind them", run: function () { scene.toggleWalls() } }
            ]
            Rectangle {
                width: caption.implicitWidth + 16
                height: 24
                radius: 4
                color: scene.panel
                border.color: scene.strong
                opacity: press.containsMouse ? 1 : 0.9
                Text {
                    id: caption
                    anchors.centerIn: parent
                    text: modelData.label === "Low walls" && scene.wallHeight < 1 ? "Tall walls" : modelData.label
                    color: scene.ink
                    font.pixelSize: 12
                }
                MouseArea {
                    id: press
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: modelData.run()
                }
            }
        }
    }
}
