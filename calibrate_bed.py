import json
import cv2
import numpy as np
import pyrealsense2 as rs

CALIBRATION_FILE = "calibration.json"

# TEST ONLY: your small 100x100 mm square
# Replace later with real bed corner coordinates
BED_POINTS_MM = np.array([
    [0.0, 0.0],       # top-left
    [400.0, 0.0],     # top-right
    [400.0, 300.0],   # bottom-right
    [0.0, 300.0],     # bottom-left
], dtype=np.float32)

clicked_points = []


def mouse_callback(event, x, y, flags, param):
    global clicked_points
    if event == cv2.EVENT_LBUTTONDOWN and len(clicked_points) < 4:
        clicked_points.append([float(x), float(y)])


def order_points(pts):
    pts = np.array(pts, dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1)

    top_left = pts[np.argmin(s)]
    bottom_right = pts[np.argmax(s)]
    top_right = pts[np.argmin(d)]
    bottom_left = pts[np.argmax(d)]

    return np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)


def main():
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)

    pipeline.start(config)

    try:
        print("Calibration view started.")
        print("Click these 4 bed markers in this exact order:")
        print("1 = top-left")
        print("2 = top-right")
        print("3 = bottom-right")
        print("4 = bottom-left")
        print("Press 'r' to reset clicks, 's' to save, 'q' to quit.")

        cv2.namedWindow("Bed Calibration")
        cv2.setMouseCallback("Bed Calibration", mouse_callback)

        frame_for_save = None

        while True:
            frames = pipeline.wait_for_frames()
            color_frame = frames.get_color_frame()
            if not color_frame:
                continue

            image = np.asanyarray(color_frame.get_data())
            frame_for_save = image.copy()
            display = image.copy()

            for i, pt in enumerate(clicked_points):
                x, y = int(pt[0]), int(pt[1])
                cv2.circle(display, (x, y), 8, (0, 0, 255), -1)
                cv2.putText(display, str(i + 1), (x + 10, y - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)

            if len(clicked_points) == 4:
                ordered = order_points(clicked_points)
                for i in range(4):
                    p1 = tuple(ordered[i].astype(int))
                    p2 = tuple(ordered[(i + 1) % 4].astype(int))
                    cv2.line(display, p1, p2, (0, 255, 0), 2)

            cv2.imshow("Bed Calibration", display)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("r"):
                clicked_points.clear()
                print("Clicks reset.")
            elif key == ord("s"):
                if len(clicked_points) != 4:
                    print("You need exactly 4 points before saving.")
                    continue

                image_points_px = order_points(clicked_points)

                H = cv2.getPerspectiveTransform(image_points_px, BED_POINTS_MM)
                H_inv = cv2.getPerspectiveTransform(BED_POINTS_MM, image_points_px)

                payload = {
                    "image_points_px": image_points_px.tolist(),
                    "bed_points_mm": BED_POINTS_MM.tolist(),
                    "homography_px_to_mm": H.tolist(),
                    "homography_mm_to_px": H_inv.tolist(),
                }

                with open(CALIBRATION_FILE, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)

                cv2.imwrite("calibration_snapshot.png", frame_for_save)
                print(f"Saved {CALIBRATION_FILE} and calibration_snapshot.png")
            elif key == ord("q"):
                break

    finally:
        pipeline.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()