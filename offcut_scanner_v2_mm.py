import cv2

from scanner import OffcutScannerEngine


def main():
    engine = OffcutScannerEngine()
    engine.start_camera()

    print("Camera started.")
    print("Controls:")
    print("  b = capture empty-bed baseline")
    print("  s = save current detected scan")
    print("  q = quit")

    try:
        while True:
            view = engine.process_next_frame()
            if view is None:
                continue

            cv2.imshow("Offcut Scanner v2 - Preview", view.preview_image)
            cv2.imshow("Offcut Scanner v2 - Mask", view.mask_image)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("b"):
                engine.capture_baseline()
                print("Baseline captured.")
            elif key == ord("s"):
                if view.scan_result is not None:
                    saved = engine.save_scan_result(view.scan_result)
                    print("\nSaved scan:")
                    print(f"  Image: {saved['image_path']}")
                    print(f"  Mask : {saved['mask_path']}")
                    print(f"  JSON : {saved['json_path']}")
                    print(saved["payload"])
                else:
                    print("No valid contour to save.")
            elif key == ord("q"):
                break
    finally:
        engine.stop_camera()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
