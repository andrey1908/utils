import argparse
import numpy as np
import cv2
import glob
import os
import os.path as osp
from pathlib import Path
from copy import deepcopy


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('-fld', '--images-folder', required=True, type=str)
    parser.add_argument('-ext', '--images-extension', type=str, default='jpg')
    parser.add_argument('-calib', '--camera-calibration', required=True, type=str)
    parser.add_argument('-size', '--aruco-size', required=True, type=float)
    parser.add_argument('-out-fld', '--out-folder', required=True, type=str)
    return parser

class ArucoList:
    # self.corners.shape = (n, 1, 4, 2)
    # self.ids.shape = (n, 1)
    # self.rejected.shape = (n_rejected, 1, 4, 2)
    # self.aruco_sizes.shape = (n,)
    # self.rvecs.shape = (n, n_poses, 3)
    # self.tvecs.shape = (n, n_poses, 3)
    # self.reprojection_errors = (n, n_poses)

    def __init__(self):
        self.reset()

    def reset(self):
        self.n = -1
        self.corners = None
        self.ids = None

        self.n_rejected = -1
        self.rejected = None

        self.aruco_sizes = None
        self.n_poses = -1
        self.rvecs = None
        self.tvecs = None
        self.reprojection_errors = None


class PoseSelectors:
    def _select_by_rotation_matrix(rvec, get_rotation_matrix_score):
        scores = list()
        n_poses = rvec.shape[0]
        for i in range(n_poses):
            R, _ = cv2.Rodrigues(rvec[i])
            score = get_rotation_matrix_score(R)
            scores.append(score)

        scores = np.array(scores)
        selected = scores.argmax()
        return selected

    def Z_axis_up(rvec, tvec, reprojection_error):
        # rvec.shape = (n_poses, 3)
        # tvec.shape = (n_poses, 3)
        # reprojection_error.shape = (n_poses,)
        def get_rotation_matrix_score(R):
            score = -R[:, 2][1]  # minus Y component of aruco Z axis
            return score
        selected = PoseSelectors._select_by_rotation_matrix(
            rvec, get_rotation_matrix_score)
        return selected

    def best(rvec, tvec, reprojection_error):
        selected = np.argmin(reprojection_error)
        return selected

    def worst(rvec, tvec, reprojection_error):
        selected = np.argmax(reprojection_error)
        return selected


def detect_aruco(image, K=None, D=None, aruco_sizes=None, use_generic=False,
        aruco_dict=cv2.aruco.Dictionary_get(cv2.aruco.DICT_5X5_1000),
        params=cv2.aruco.DetectorParameters_create()):
    corners, ids, rejected = \
        cv2.aruco.detectMarkers(image, aruco_dict, parameters=params)
    n = len(corners)
    n_rejected = len(rejected)

    if not use_generic:
        n_poses = 1
    else:
        n_poses = 2

    if n != 0:
        corners = np.array(corners)
        ids = np.array(ids)
        # corners.shape = (n, 1, 4, 2)
        # ids.shape = (n, 1)

        ind = np.argsort(ids, axis=0)
        ids = np.take_along_axis(ids, ind, axis=0)
        corners = np.take_along_axis(corners, np.expand_dims(ind, axis=(-1, -2)), axis=0)

        # estimate 3d poses
        if all(item is not None for item in (K, D, aruco_sizes)):
            if isinstance(aruco_sizes, (list, tuple)):
                aruco_sizes = np.array(aruco_sizes)
            elif not isinstance(aruco_sizes, np.ndarray):
                aruco_sizes = np.array([aruco_sizes] * n)
            if len(aruco_sizes.shape) != 1:
                raise RuntimeError(f"Use list, tuple or np.ndarray to pass multiple aruco sizes.")
            if aruco_sizes.shape != (n,):
                raise RuntimeError(
                    f"Number of aruco marker sizes does not correspond to "
                    f"the number of detected markers ({aruco_sizes.shape[0]} vs {n})")

            rvecs = list()
            tvecs = list()
            reprojection_errors = list()
            for i in range(n):
                aruco_size = aruco_sizes[i]
                obj = np.array([
                    [-aruco_size / 2,  aruco_size / 2, 0],
                    [ aruco_size / 2,  aruco_size / 2, 0],
                    [ aruco_size / 2, -aruco_size / 2, 0],
                    [-aruco_size / 2, -aruco_size / 2, 0]])
                if not use_generic:
                    retval, rvec, tvec = \
                        cv2.solvePnP(obj, corners[i], K, D,
                            flags=cv2.SOLVEPNP_IPPE_SQUARE)
                    rvec = rvec.swapaxes(0, 1)
                    tvec = tvec.swapaxes(0, 1)
                    reprojection_error = np.array([-1])  # undefined
                    # rvec.shape = (n_poses, 3)
                    # tvec.shape = (n_poses, 3)
                    # reprojection_error.shape = (n_poses,)
                else:
                    retval, rvec, tvec, reprojection_error = \
                        cv2.solvePnPGeneric(obj, corners[i], K, D,
                            flags=cv2.SOLVEPNP_IPPE_SQUARE,
                            reprojectionError=np.empty(0, dtype=np.float))
                    assert len(rvec) == n_poses
                    rvec = np.array(rvec)
                    tvec = np.array(tvec)
                    # rvec.shape = (n_poses, 3, 1)
                    # tvec.shape = (n_poses, 3, 1)
                    # reprojection_error.shape = (n_poses, 1)

                    rvec = rvec.squeeze()
                    tvec = tvec.squeeze()
                    reprojection_error = reprojection_error.squeeze()
                    # rvec.shape = (n_poses, 3)
                    # tvec.shape = (n_poses, 3)
                    # reprojection_error.shape = (n_poses,)
                rvecs.append(rvec)
                tvecs.append(tvec)
                reprojection_errors.append(reprojection_error)
            rvecs = np.array(rvecs)
            tvecs = np.array(tvecs)
            reprojection_errors = np.array(reprojection_errors)
            # rvecs.shape = (n, n_poses, 3)
            # tvecs.shape = (n, n_poses, 3)
            # reprojection_errors.shape = (n, n_poses)
        else:
            n_poses = 0
            aruco_sizes = None
            rvecs = None
            tvecs = None
            reprojection_errors = None
    else:
        corners = np.empty((0, 1, 4, 2))
        ids = np.empty((0, 1))
        aruco_sizes = np.empty((0,))
        rvecs = np.empty((0, n_poses, 3))
        tvecs = np.empty((0, n_poses, 3))
        reprojection_errors = np.empty((0, n_poses))

    if n_rejected != 0:
        rejected = np.array(rejected)
        # rejected.shape = (n_rejected, 1, 4, 2)
    else:
        rejected = np.empty((0, 1, 4, 2))

    arucos = ArucoList()
    arucos.n = n
    arucos.corners = corners
    arucos.ids = ids
    arucos.n_rejected = n_rejected
    arucos.rejected = rejected
    arucos.aruco_sizes = aruco_sizes
    arucos.n_poses = n_poses
    arucos.rvecs = rvecs
    arucos.tvecs = tvecs
    arucos.reprojection_errors = reprojection_errors
    return arucos


def get_aruco_corners_3d(arucos: ArucoList):
    if any(getattr(arucos, attr) is None for attr in ('aruco_sizes', 'rvecs', 'tvecs')):
        return None

    n = arucos.n
    aruco_sizes = arucos.aruco_sizes
    n_poses = arucos.n_poses
    rvecs = arucos.rvecs
    tvecs = arucos.tvecs

    if n != 0:
        marker_poses = np.tile(np.eye(4), (n, n_poses, 1, 1))
        for i in range(n):
            for j in range(n_poses):
                marker_poses[i, j, 0:3, 0:3], _ = cv2.Rodrigues(rvecs[i, j])
                marker_poses[i, j, 0:3, 3] = tvecs[i, j]
        # marker_poses.shape = (n, n_poses, 4, 4)

        corners_3d_in_marker_frames = list()
        for i in range(n):
            corners_3d_in_single_marker_frame = list()
            aruco_size = aruco_sizes[i]
            for sx, sy in [(-1, 1), (1, 1), (1, -1), (-1, -1)]:
                single_corner_3d_in_marker_frame = np.array([
                    aruco_size / 2 * sx,
                    aruco_size / 2 * sy,
                    0, 1]).reshape(4, 1)
                corners_3d_in_single_marker_frame.append(single_corner_3d_in_marker_frame)
            corners_3d_in_single_marker_frame = np.array(corners_3d_in_single_marker_frame)
            corners_3d_in_marker_frames.append(corners_3d_in_single_marker_frame)
        corners_3d_in_marker_frames = np.array(corners_3d_in_marker_frames)
        # corners_3d_in_marker_frames.shape = (n, 4, 4, 1)

        corners_3d_in_marker_frames = \
            np.expand_dims(corners_3d_in_marker_frames.swapaxes(0, 1), axis=2)
        # corners_3d_in_marker_frames.shape = (4, n, 1, 4, 1)

        corners_3d = np.matmul(marker_poses, corners_3d_in_marker_frames)
        corners_3d = corners_3d[:, :, :, 0:3, 0].transpose(1, 2, 0, 3)
        # corners_3d.shape = (n, n_poses, 4, 3)
    else:
        corners_3d = np.empty((0, n_poses, 4, 3))

    # corners_3d.shape = (n, n_poses, 4, 3)
    return corners_3d


def select_aruco_poses(arucos: ArucoList, selector):
    n = arucos.n
    if n == 0:
        arucos_selected = deepcopy(arucos)
        arucos_selected.n_poses = 1
        arucos_selected.rvecs = np.empty((0, 1, 3))
        arucos_selected.tvecs = np.empty((0, 1, 3))
        arucos_selected.reprojection_errors = np.empty((0, 1, 3))
        return arucos_selected

    rvecs = arucos.rvecs
    tvecs = arucos.tvecs
    reprojection_errors = arucos.reprojection_errors
    selected = list()
    for i in range(n):
        s = selector(rvecs[i], tvecs[i], reprojection_errors[i])
        selected.append(s)

    selected = np.array(selected).reshape(n, 1, 1)
    arucos_selected = deepcopy(arucos)
    arucos_selected.n_poses = 1
    arucos_selected.rvecs = np.take_along_axis(rvecs, selected, axis=1)
    arucos_selected.tvecs = np.take_along_axis(tvecs, selected, axis=1)
    arucos_selected.reprojection_errors = \
        np.take_along_axis(reprojection_errors, selected.reshape(n, 1), axis=1)
    return arucos_selected


def draw_aruco(image, arucos: ArucoList, draw_rejected_only=False,
        draw_ids=False, K=None, D=None):
    if arucos.n_poses > 1:
        raise RuntimeError(
            f"Use select_poses() to recude number of poses to 1 "
            f"(now it is {arucos.n_poses})")

    if draw_rejected_only:
        cv2.aruco.drawDetectedMarkers(image, arucos.rejected)
    else:
        if draw_ids:
            cv2.aruco.drawDetectedMarkers(image, arucos.corners, arucos.ids)
        else:
            cv2.aruco.drawDetectedMarkers(image, arucos.corners)
        if all(item is not None for item in (arucos.aruco_sizes, K, D)):
            for i in range(arucos.n):
                cv2.drawFrameAxes(image, K, D,
                    arucos.rvecs[i], arucos.tvecs[i], arucos.aruco_sizes[i] / 2)
    return image


def detect_aruco_common(images_files, K, D, aruco_size, out_folder):
    os.makedirs(out_folder, exist_ok=True)

    aruco_dict = cv2.aruco.Dictionary_get(cv2.aruco.DICT_4X4_1000)
    params = cv2.aruco.DetectorParameters_create()
    for image_file in images_files:
        image = cv2.imread(image_file)
        arucos = detect_aruco(image, K=K, D=D, aruco_sizes=aruco_size,
            use_generic=False, aruco_dict=aruco_dict, params=params)

        n = arucos.n
        print(f"{image_file} : detected {n} marker{'' if n == 1 else 's'}")

        draw = draw_aruco(image.copy(), arucos, K=K, D=D)
        draw_image_file = osp.join(
            out_folder, Path(image_file).stem + '_vis.jpg')
        cv2.imwrite(draw_image_file, draw)


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()

    images_files = glob.glob(args.images_folder + f"/*.{args.images_extension}")
    images_files = sorted(images_files)
    assert len(images_files) != 0

    camera_calibration = np.load(args.camera_calibration)
    K = camera_calibration['K']
    D = camera_calibration['D']

    detect_aruco_common(images_files, K, D, args.aruco_size, args.out_folder)
